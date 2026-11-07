//! Workflow execution engine (n8n "v1" execution order: depth first, branch by branch).
//! Every execution runs on its own thread with a single-threaded Tokio runtime so the
//! JavaScript sandbox (which is !Send) can live next to async node code.

use crate::app::{App, AppState};
use crate::js::{Hooks, Js, Part, parse_template};
use crate::model::*;
use crate::nodes;
use anyhow::{Result, anyhow};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::sync::oneshot;

#[derive(Default, Clone, Serialize, Deserialize)]
pub struct StartSpec {
    pub start_node: Option<String>,
    pub items: Option<Vec<Item>>,
    pub destination: Option<String>,
    /// Continue a parked execution: saved state plus the output of the Wait node.
    pub resume: Option<(Snapshot, Vec<Item>)>,
    /// Run data of the execution so far (for `$('Node')` after a resume).
    pub prev_run_data: Option<RunData>,
}

/// Raised by the Wait node to park the execution in the database.
#[derive(Debug)]
pub struct Park {
    pub kind: String,
    pub resume_at: Option<i64>,
}

impl std::fmt::Display for Park {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "waiting ({})", self.kind)
    }
}

impl std::error::Error for Park {}

/// Everything needed to continue an execution after a Wait node, possibly after a restart.
#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Snapshot {
    stack: VecDeque<Job>,
    waiting: Vec<(String, Vec<Option<Vec<Item>>>, Vec<Value>)>,
    node_state: HashMap<String, Value>,
    pub wait_node: String,
    wait_source: Vec<Value>,
    pub wait_input: Vec<Item>,
    pub kind: String,
    pub resume_at: Option<i64>,
}

/// HTTP answer produced by a webhook execution (Respond to Webhook node).
#[derive(Debug, Clone)]
pub struct Reply {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

pub struct Outcome {
    pub status: String,
    pub error: Option<Value>,
    pub last_node: Option<String>,
}

#[derive(Serialize, Deserialize, Clone)]
struct Job {
    node: String,
    inputs: Vec<Vec<Item>>,
    source: Vec<Value>,
    /// Output known in advance (the Wait node when resuming); the node is not executed.
    #[serde(default)]
    preset: Option<Vec<Vec<Item>>>,
}

pub struct Engine {
    pub app: AppState,
    pub wf: Rc<Workflow>,
    pub mode: String,
    pub exec_id: i64,
    pub run_data: Rc<RefCell<RunData>>,
    pub js: Js,
    pub node_state: RefCell<HashMap<String, Value>>,
    pub reply: RefCell<Option<oneshot::Sender<Reply>>>,
    pub cancel: Arc<AtomicBool>,
    pub static_data: RefCell<Value>,
    pub logs: Rc<RefCell<Vec<String>>>,
    pub depth: u32,
    /// User whose credentials this execution may use (manual runner or workflow owner).
    pub user: Option<String>,
    /// Set when a Wait node parked the execution.
    pub parked: RefCell<Option<Snapshot>>,
    /// Key/values from the Execution Data node, stored with the execution.
    pub custom_data: RefCell<serde_json::Map<String, Value>>,
}

impl Engine {
    pub fn new(app: AppState, wf: Workflow, mode: &str, exec_id: i64, cancel: Arc<AtomicBool>, depth: u32) -> Result<Engine> {
        let static_data = if wf.static_data.is_object() { wf.static_data.clone() } else { json!({}) };
        let wf = Rc::new(wf);
        let run_data: Rc<RefCell<RunData>> = Rc::default();
        let logs: Rc<RefCell<Vec<String>>> = Rc::default();

        let (rd, w) = (run_data.clone(), wf.clone());
        let node_out = Rc::new(move |name: &str, branch: usize, run: i64| -> String {
            if w.node(name).is_none() {
                return "!!".into();
            }
            let rd = rd.borrow();
            let task = rd.get(name).and_then(|runs| if run < 0 { runs.last() } else { runs.get(run as usize) });
            match task {
                Some(t) => t.data.get("main").and_then(|m| m.get(branch)).map(|b| b.to_string()).unwrap_or_else(|| "[]".into()),
                None => "!".into(),
            }
        });
        let w = wf.clone();
        let node_params = Rc::new(move |name: &str| w.node(name).map(|n| n.parameters.to_string()).unwrap_or_else(|| "{}".into()));
        let block_env = app.cfg.block_env;
        let env = Rc::new(move || {
            if block_env {
                return "{}".into();
            }
            let m: serde_json::Map<String, Value> = std::env::vars()
                .filter(|(k, _)| !k.contains("ENCRYPTION_KEY"))
                .map(|(k, v)| (k, Value::String(v)))
                .collect();
            Value::Object(m).to_string()
        });
        let vars_json = {
            let m: serde_json::Map<String, Value> = app
                .db
                .variables()
                .unwrap_or_default()
                .into_iter()
                .map(|v| (as_str(&v["key"]), v["value"].clone()))
                .collect();
            Value::Object(m).to_string()
        };
        let vars = Rc::new(move || vars_json.clone());
        let (l, a) = (logs.clone(), app.clone());
        let log = Rc::new(move |s: String| {
            tracing::info!(execution = exec_id, "console: {s}");
            a.push(json!({"type": "console", "data": {"executionId": exec_id.to_string(), "message": s}}));
            l.borrow_mut().push(s);
        });
        let js = Js::new(Hooks { node_out, node_params, env, vars, log })?;
        Ok(Engine {
            app,
            wf,
            mode: mode.to_string(),
            exec_id,
            run_data,
            js,
            node_state: RefCell::default(),
            reply: RefCell::default(),
            cancel,
            static_data: RefCell::new(static_data),
            logs,
            depth,
            user: None,
            parked: RefCell::default(),
            custom_data: RefCell::default(),
        })
    }

    /// Evaluates expressions (`=...{{ }}`) inside a parameter value for item `i`.
    pub fn resolve(&self, v: &Value, i: usize) -> Result<Value> {
        Ok(match v {
            Value::String(s) if s.starts_with('=') => self.template(&s[1..], i)?,
            Value::Array(a) => Value::Array(a.iter().map(|x| self.resolve(x, i)).collect::<Result<_>>()?),
            Value::Object(m) => {
                let mut o = serde_json::Map::new();
                for (k, x) in m {
                    o.insert(k.clone(), self.resolve(x, i)?);
                }
                Value::Object(o)
            }
            other => other.clone(),
        })
    }

    pub fn template(&self, s: &str, i: usize) -> Result<Value> {
        let parts = parse_template(s);
        if let [Part::Code(c)] = parts.as_slice() {
            return self.js.eval(c, i);
        }
        let mut out = String::new();
        for p in parts {
            match p {
                Part::Text(t) => out.push_str(&t),
                Part::Code(c) => out.push_str(&as_str(&self.js.eval(&c, i)?)),
            }
        }
        Ok(Value::String(out))
    }

    fn emit(&self, kind: &str, data: Value) {
        if self.depth == 0 {
            let mut d = data;
            d["executionId"] = json!(self.exec_id.to_string());
            d["workflowId"] = json!(self.wf.id);
            self.app.push(json!({"type": kind, "data": d}));
        }
    }

    fn pinned(&self, name: &str) -> Option<Vec<Item>> {
        self.wf.pin_data.get(name).filter(|v| v.is_array()).map(|v| to_items(v.clone()))
    }

    fn start_nodes(&self, spec: &StartSpec, conns: &Connections) -> Vec<String> {
        if let Some(s) = &spec.start_node {
            return vec![s.clone()];
        }
        let enabled = |n: &&Node| !n.disabled && n.kind != "n8n-nodes-base.stickyNote";
        // Restrict to ancestors of the destination node when one is given.
        let allowed: Option<HashSet<String>> = spec.destination.as_ref().map(|d| ancestors(d, conns));
        let ok = |n: &&Node| allowed.as_ref().is_none_or(|a| a.contains(&n.name));
        let pick = |pred: &dyn Fn(&Node) -> bool| -> Vec<String> {
            self.wf.nodes.iter().filter(enabled).filter(ok).filter(|n| pred(n)).map(|n| n.name.clone()).collect()
        };
        let manual = pick(&|n| n.kind == "n8n-nodes-base.manualTrigger");
        if !manual.is_empty() {
            return manual;
        }
        let triggers = pick(&|n| nodes::is_trigger(&n.kind));
        if let Some(t) = triggers.into_iter().next() {
            return vec![t];
        }
        let targets: HashSet<&str> = conns
            .values()
            .flat_map(|m| m.get("main").into_iter().flatten().flatten().flatten())
            .map(|c| c.node.as_str())
            .collect();
        pick(&|n| !targets.contains(n.name.as_str()))
    }

    pub async fn run(&self, spec: StartSpec) -> Outcome {
        let conns = self.wf.conns();
        let mut in_count: HashMap<String, BTreeSet<usize>> = HashMap::new();
        for outs in conns.values() {
            for c in outs.get("main").into_iter().flatten().flatten().flatten() {
                in_count.entry(c.node.clone()).or_default().insert(c.index);
            }
        }
        let mut stack: VecDeque<Job> = VecDeque::new();
        let mut waiting: Vec<(String, Vec<Option<Vec<Item>>>, Vec<Value>)> = vec![];
        if let Some(prev) = spec.prev_run_data.clone() {
            *self.run_data.borrow_mut() = prev;
        }
        if let Some((snap, items)) = spec.resume.clone() {
            stack = snap.stack;
            waiting = snap.waiting;
            *self.node_state.borrow_mut() = snap.node_state;
            // Drop the placeholder run of the Wait node; it is recorded again with its real output.
            if let Some(runs) = self.run_data.borrow_mut().get_mut(&snap.wait_node) {
                runs.pop();
            }
            stack.push_front(Job { node: snap.wait_node, inputs: vec![snap.wait_input], source: snap.wait_source, preset: Some(vec![items]) });
        } else {
            for name in self.start_nodes(&spec, &conns) {
                let Some(n) = self.wf.node(&name) else { continue };
                let items = spec.items.clone().unwrap_or_else(|| {
                    if nodes::is_trigger(&n.kind) { vec![] } else { vec![Item::new(json!({}))] }
                });
                stack.push_back(Job { node: name, inputs: vec![items], source: vec![], preset: None });
            }
        }
        if stack.is_empty() {
            return Outcome { status: "error".into(), error: Some(json!({"message": "Workflow has no node to start from"})), last_node: None };
        }

        let mut last_node = None;
        loop {
            while let Some(job) = stack.pop_front() {
                if self.cancel.load(Ordering::Relaxed) {
                    return Outcome { status: "canceled".into(), error: Some(json!({"message": "Execution was canceled"})), last_node };
                }
                let Some(node) = self.wf.node(&job.node).cloned() else { continue };
                let run_index = self.run_data.borrow().get(&node.name).map_or(0, |v| v.len());
                self.emit("nodeExecuteBefore", json!({"nodeName": node.name}));
                let started = now_ms();
                let res = match job.preset.clone() {
                    Some(p) => Ok(p),
                    None => self.exec_node(&node, job.inputs.clone(), run_index, &job.source).await,
                };
                let mut task = TaskData {
                    start_time: started,
                    execution_time: now_ms() - started,
                    execution_status: "success".into(),
                    source: job.source.clone(),
                    ..Default::default()
                };
                last_node = Some(node.name.clone());
                let outputs = match res {
                    Ok(o) => o,
                    Err(e) if e.downcast_ref::<Park>().is_some() => {
                        let p = e.downcast_ref::<Park>().unwrap();
                        task.execution_status = "waiting".into();
                        task.data = json!({"main": [job.inputs.first().cloned().unwrap_or_default()]});
                        self.run_data.borrow_mut().entry(node.name.clone()).or_default().push(task);
                        self.emit("nodeExecuteAfter", json!({"nodeName": node.name, "status": "waiting"}));
                        *self.parked.borrow_mut() = Some(Snapshot {
                            stack,
                            waiting,
                            node_state: self.node_state.borrow().clone(),
                            wait_node: node.name.clone(),
                            wait_source: job.source.clone(),
                            wait_input: job.inputs.first().cloned().unwrap_or_default(),
                            kind: p.kind.clone(),
                            resume_at: p.resume_at,
                        });
                        return Outcome { status: "waiting".into(), error: None, last_node };
                    }
                    // A node aborted by "stop" is a cancellation, not a node error.
                    Err(_) if self.cancel.load(Ordering::Relaxed) => {
                        return Outcome { status: "canceled".into(), error: Some(json!({"message": "Execution was canceled"})), last_node };
                    }
                    Err(e) => {
                        let err = json!({"message": e.to_string(), "node": node.name, "timestamp": now_ms()});
                        let input = job.inputs.first().cloned().unwrap_or_default();
                        let error_items = |merge: bool| -> Vec<Item> {
                            if input.is_empty() {
                                return vec![Item::new(json!({"error": e.to_string()}))];
                            }
                            input
                                .iter()
                                .enumerate()
                                .map(|(i, it)| {
                                    let mut j = if merge && it.json.is_object() { it.json.clone() } else { json!({}) };
                                    j["error"] = json!(e.to_string());
                                    Item::paired(j, i)
                                })
                                .collect()
                        };
                        match node.on_error_mode() {
                            "continueRegularOutput" => {
                                task.error = Some(err);
                                vec![error_items(false)]
                            }
                            "continueErrorOutput" => {
                                task.error = Some(err);
                                let n = nodes::output_count(&node);
                                let mut o = vec![vec![]; n];
                                o.push(error_items(true));
                                o
                            }
                            _ => {
                                task.execution_status = "error".into();
                                task.error = Some(err.clone());
                                self.emit("nodeExecuteAfter", json!({"nodeName": node.name, "status": "error", "error": err}));
                                self.run_data.borrow_mut().entry(node.name.clone()).or_default().push(task);
                                return Outcome { status: "error".into(), error: Some(err), last_node };
                            }
                        }
                    }
                };
                let counts: Vec<usize> = outputs.iter().map(|o| o.len()).collect();
                task.data = json!({ "main": outputs });
                self.emit("nodeExecuteAfter", json!({"nodeName": node.name, "status": task.execution_status, "itemCounts": counts, "executionTime": task.execution_time}));
                self.run_data.borrow_mut().entry(node.name.clone()).or_default().push(task);

                if spec.destination.as_deref() == Some(node.name.as_str()) {
                    return Outcome { status: "success".into(), error: None, last_node };
                }

                // Schedule children: output 0 first, depth first.
                let mut next = vec![];
                let outs = conns.get(&node.name).and_then(|m| m.get("main"));
                for (idx, items) in outputs.iter().enumerate() {
                    if items.is_empty() {
                        continue;
                    }
                    let targets = outs.and_then(|o| o.get(idx)).and_then(|x| x.as_ref());
                    for c in targets.into_iter().flatten() {
                        let Some(child) = self.wf.node(&c.node) else { continue };
                        let src = json!({"previousNode": node.name, "previousNodeOutput": idx});
                        let needed = in_count.get(&c.node).cloned().unwrap_or_default();
                        if needed.len() <= 1 || child.disabled {
                            let mut inputs = vec![vec![]; c.index + 1];
                            inputs[c.index] = items.clone();
                            if child.disabled {
                                inputs = vec![items.clone()];
                            }
                            next.push(Job { node: c.node.clone(), inputs, source: vec![src], preset: None });
                            continue;
                        }
                        let size = needed.iter().max().copied().unwrap_or(0) + 1;
                        let pos = waiting.iter().position(|(n, ins, _)| *n == c.node && ins.get(c.index).is_none_or(|x| x.is_none()));
                        let pos = pos.unwrap_or_else(|| {
                            waiting.push((c.node.clone(), vec![None; size], vec![]));
                            waiting.len() - 1
                        });
                        let w = &mut waiting[pos];
                        w.1[c.index] = Some(items.clone());
                        w.2.push(src);
                        if needed.iter().all(|i| w.1.get(*i).is_some_and(|x| x.is_some())) {
                            let (n, ins, s) = waiting.remove(pos);
                            next.push(Job { node: n, inputs: ins.into_iter().map(|x| x.unwrap_or_default()).collect(), source: s, preset: None });
                        }
                    }
                }
                for j in next.into_iter().rev() {
                    stack.push_front(j);
                }
            }
            // Nodes still waiting for a branch that never produced data run with what they have.
            if waiting.is_empty() {
                break;
            }
            let (n, ins, s) = waiting.remove(0);
            stack.push_back(Job { node: n, inputs: ins.into_iter().map(|x| x.unwrap_or_default()).collect(), source: s, preset: None });
        }
        Outcome { status: "success".into(), error: None, last_node }
    }

    async fn exec_node(&self, node: &Node, mut inputs: Vec<Vec<Item>>, run_index: usize, source: &[Value]) -> Result<Vec<Vec<Item>>> {
        if node.disabled {
            return Ok(vec![inputs.into_iter().next().unwrap_or_default()]);
        }
        if self.mode == "manual" {
            if let Some(p) = self.pinned(&node.name) {
                return Ok(vec![p]);
            }
        }
        if node.execute_once {
            inputs.iter_mut().for_each(|i| i.truncate(1));
        }
        let main = inputs.iter().find(|i| !i.is_empty()).cloned().unwrap_or_default();
        let ctx = json!({
            "runIndex": run_index,
            "node": node.name,
            "parameters": node.parameters,
            "prevNode": source.first().map(|s| json!({"name": s["previousNode"], "outputIndex": s["previousNodeOutput"], "runIndex": 0})),
            "workflow": {"id": self.wf.id, "name": self.wf.name, "active": self.wf.active},
            "execution": {"id": self.exec_id.to_string(), "mode": if self.mode == "manual" { "test" } else { "production" },
                          "resumeUrl": format!("{}/webhook-waiting/{}", self.app.cfg.base_url, self.exec_id),
                          "resumeFormUrl": format!("{}/form-waiting/{}", self.app.cfg.base_url, self.exec_id)},
        });
        self.js.set_input(&serde_json::to_string(&main)?, &ctx.to_string())?;

        let tries = if node.retry_on_fail { node.max_tries.unwrap_or(3).clamp(1, 5) } else { 1 };
        let mut last = anyhow!("node did not run");
        for t in 0..tries {
            if t > 0 {
                let wait = node.wait_between_tries.unwrap_or(1000).min(5000);
                tokio::time::sleep(std::time::Duration::from_millis(wait)).await;
            }
            match nodes::execute(self, node, inputs.clone()).await {
                Ok(mut out) => {
                    if node.always_output_data && out.iter().all(|o| o.is_empty()) {
                        if out.is_empty() {
                            out.push(vec![]);
                        }
                        out[0].push(Item::new(json!({})));
                    }
                    return Ok(out);
                }
                Err(e) => last = e,
            }
        }
        Err(last)
    }

    /// Payload stored in the executions table (n8n IRunExecutionData shape).
    pub fn result_data(&self, o: &Outcome) -> Value {
        json!({
            "resultData": {
                "runData": *self.run_data.borrow(),
                "lastNodeExecuted": o.last_node,
                "error": o.error,
            },
            "logs": *self.logs.borrow(),
            "customData": *self.custom_data.borrow(),
        })
    }
}

fn ancestors(dest: &str, conns: &Connections) -> HashSet<String> {
    let mut parents: HashMap<&str, Vec<&str>> = HashMap::new();
    for (src, m) in conns {
        for c in m.get("main").into_iter().flatten().flatten().flatten() {
            parents.entry(c.node.as_str()).or_default().push(src.as_str());
        }
    }
    let mut seen = HashSet::new();
    let mut todo = vec![dest];
    while let Some(n) = todo.pop() {
        if seen.insert(n.to_string()) {
            todo.extend(parents.get(n).into_iter().flatten().copied());
        }
    }
    seen
}

pub struct Launch {
    pub wf: Workflow,
    pub mode: String,
    pub spec: StartSpec,
    pub reply: Option<oneshot::Sender<Reply>>,
    pub retry_of: Option<i64>,
    pub user: Option<String>,
    /// Continue this existing execution instead of creating a new one.
    pub exec_id: Option<i64>,
}

/// Final state handed back to whoever started an execution.
pub struct Finished {
    pub status: String,
    pub data: Value,
}

/// Starts an execution in the background. Returns its id and a receiver for the result.
pub fn launch(app: AppState, l: Launch) -> Result<(i64, oneshot::Receiver<Finished>)> {
    let exec_id = match l.exec_id {
        Some(id) => {
            app.db.set_status(id, "running")?;
            id
        }
        None => app.db.create_execution(&l.wf, &l.mode, l.retry_of)?,
    };
    let (tx, rx) = oneshot::channel();
    if app.cfg.queue_mode && app.worker.is_none() && l.mode != "manual" && l.mode != "integrated" {
        let payload = json!({"mode": l.mode, "spec": serde_json::to_value(&l.spec)?, "user": l.user, "wantsReply": l.reply.is_some()});
        app.db.enqueue(exec_id, &payload)?;
        app.push(json!({"type": "executionQueued", "data": {"executionId": exec_id.to_string(), "workflowId": l.wf.id}}));
        app.rt.clone().spawn(watch_queued(app, exec_id, l.reply, tx));
        return Ok((exec_id, rx));
    }
    let cancel = Arc::new(AtomicBool::new(false));
    app.running.lock().unwrap().insert(exec_id, cancel.clone());
    app.push(json!({"type": "executionStarted", "data": {"executionId": exec_id.to_string(), "workflowId": l.wf.id, "mode": l.mode}}));
    // Production executions wait for a free slot when a concurrency limit is set.
    let slots = if l.mode == "manual" || l.mode == "integrated" { None } else { app.prod_slots.clone() };
    let rt = app.rt.clone();
    rt.spawn(async move {
        let _permit = match slots {
            Some(s) => {
                let queued = s.available_permits() == 0;
                if queued {
                    let _ = app.db.set_status(exec_id, "queued");
                    app.push(json!({"type": "executionQueued", "data": {"executionId": exec_id.to_string()}}));
                }
                let p = s.acquire_owned().await.ok();
                if queued {
                    let _ = app.db.set_status(exec_id, "running");
                }
                p
            }
            None => None,
        };
        let a = app.clone();
        let handle = tokio::task::spawn_blocking(move || {
            let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("runtime");
            rt.block_on(run_in_thread(a, l, exec_id, cancel))
        });
        let fin = match handle.await {
            Ok(f) => f,
            Err(e) => {
                let data = json!({"resultData": {"runData": {}, "error": {"message": format!("execution crashed: {e}")}}});
                let _ = app.db.finish_execution(exec_id, "crashed", &data);
                Finished { status: "crashed".into(), data }
            }
        };
        app.running.lock().unwrap().remove(&exec_id);
        app.push(json!({"type": "executionFinished", "data": {"executionId": exec_id.to_string(), "status": fin.status}}));
        let _ = tx.send(fin);
    });
    Ok((exec_id, rx))
}

/// Main process in queue mode: relays the worker's webhook reply and final state.
async fn watch_queued(app: AppState, exec_id: i64, mut reply: Option<oneshot::Sender<Reply>>, tx: oneshot::Sender<Finished>) {
    loop {
        tokio::time::sleep(std::time::Duration::from_millis(150)).await;
        if reply.is_some() {
            if let Ok(Some((status, headers, body))) = app.db.take_reply(exec_id) {
                let headers = headers.as_object().into_iter().flatten().map(|(k, v)| (k.clone(), as_str(v))).collect();
                let _ = reply.take().unwrap().send(Reply { status, headers, body });
            }
        }
        let status = app.db.status_of(exec_id).ok().flatten().unwrap_or_default();
        if matches!(status.as_str(), "queued" | "running" | "canceling") {
            continue;
        }
        // One more look for a reply written just before the execution finished.
        if let (Some(r), Ok(Some((status, headers, body)))) = (reply.take(), app.db.take_reply(exec_id)) {
            let headers = headers.as_object().into_iter().flatten().map(|(k, v)| (k.clone(), as_str(v))).collect();
            let _ = r.send(Reply { status, headers, body });
        }
        let data = app.db.execution(exec_id).ok().flatten().map(|e| e["data"].clone()).unwrap_or(Value::Null);
        app.push(json!({"type": "executionFinished", "data": {"executionId": exec_id.to_string(), "status": status}}));
        let _ = tx.send(Finished { status, data });
        return;
    }
}

/// `flowwolf worker`: takes queued executions from the shared database and runs them.
pub async fn worker_loop(app: AppState) {
    let name = app.worker.clone().unwrap_or_default();
    let slots = Arc::new(tokio::sync::Semaphore::new(app.cfg.worker_concurrency));
    tracing::info!("worker {name} started (concurrency {})", app.cfg.worker_concurrency);
    loop {
        let permit = slots.clone().acquire_owned().await.expect("semaphore");
        let claimed = match app.db.claim_queued(&name) {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("claiming work failed: {e}");
                None
            }
        };
        let Some((id, wf_json, payload)) = claimed else {
            drop(permit);
            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
            continue;
        };
        let app2 = app.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(e) = run_claimed(app2.clone(), id, &wf_json, &payload).await {
                tracing::error!("execution {id} failed to start on worker: {e}");
                let _ = app2.db.finish_execution(id, "crashed", &json!({"resultData": {"runData": {}, "error": {"message": e.to_string()}}}));
            }
        });
    }
}

async fn run_claimed(app: AppState, id: i64, wf_json: &str, payload: &str) -> Result<()> {
    let wf: Workflow = serde_json::from_str(wf_json)?;
    let p: Value = serde_json::from_str(payload)?;
    let spec: StartSpec = serde_json::from_value(p["spec"].clone())?;
    tracing::info!(execution = id, "running {} on worker", wf.name);
    // "Respond to Webhook" output goes to the database, where the main process picks it up.
    let reply = if p["wantsReply"] == true {
        let (rtx, rrx) = oneshot::channel::<Reply>();
        let a = app.clone();
        tokio::spawn(async move {
            if let Ok(r) = rrx.await {
                let headers: serde_json::Map<String, Value> = r.headers.into_iter().map(|(k, v)| (k, json!(v))).collect();
                let _ = a.db.save_reply(id, r.status, &Value::Object(headers), &r.body);
            }
        });
        Some(rtx)
    } else {
        None
    };
    let user = p["user"].as_str().map(String::from);
    let (_, done) = launch(app.clone(), Launch { wf, mode: as_str(&p["mode"]), spec, reply, retry_of: None, user, exec_id: Some(id) })?;
    // Cross-process stop: the main process marks the execution as "canceling".
    let a = app.clone();
    let watch = tokio::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            if a.db.status_of(id).ok().flatten().as_deref() == Some("canceling") {
                if let Some(f) = a.running.lock().unwrap().get(&id) {
                    f.store(true, Ordering::Relaxed);
                }
            }
        }
    });
    let _ = done.await;
    watch.abort();
    Ok(())
}

async fn run_in_thread(app: AppState, l: Launch, exec_id: i64, cancel: Arc<AtomicBool>) -> Finished {
    let wf = l.wf.clone();
    let mut eng = match Engine::new(app.clone(), l.wf, &l.mode, exec_id, cancel, 0) {
        Ok(e) => e,
        Err(e) => {
            let data = json!({"resultData": {"runData": {}, "error": {"message": e.to_string()}}});
            let _ = app.db.finish_execution(exec_id, "error", &data);
            return Finished { status: "error".into(), data };
        }
    };
    eng.user = l.user.or_else(|| app.db.owner_of("workflows", &wf.id).ok().flatten());
    *eng.reply.borrow_mut() = l.reply;
    let out = eng.run(l.spec).await;
    let data = eng.result_data(&out);
    if out.status == "waiting" {
        let snap = eng.parked.borrow_mut().take().unwrap_or_default();
        if let Err(e) = app.db.park_execution(exec_id, &data, &serde_json::to_value(&snap).unwrap_or_default(), &snap.kind, snap.resume_at) {
            tracing::error!("parking execution {exec_id} failed: {e}");
        }
        return Finished { status: out.status, data };
    }
    if let Err(e) = app.db.finish_execution(exec_id, &out.status, &data) {
        tracing::error!("saving execution {exec_id} failed: {e}");
    }
    if l.mode != "manual" && !wf.id.is_empty() {
        let sd = eng.static_data.borrow().clone();
        if sd != wf.static_data && !(sd == json!({}) && wf.static_data.is_null()) {
            let _ = app.db.save_static_data(&wf.id, &sd);
        }
    }
    if out.status == "error" && l.mode != "manual" {
        start_error_workflow(&app, &wf, exec_id, &l.mode, &out);
    }
    Finished { status: out.status, data }
}

fn start_error_workflow(app: &AppState, wf: &Workflow, exec_id: i64, mode: &str, out: &Outcome) {
    let Some(id) = wf.setting("errorWorkflow").map(as_str).filter(|s| !s.is_empty()) else { return };
    let Ok(Some(ewf)) = app.db.workflow(&id) else { return };
    let Some(trigger) = ewf.nodes.iter().find(|n| n.kind == "n8n-nodes-base.errorTrigger").map(|n| n.name.clone()) else { return };
    let item = Item::new(json!({
        "execution": {"id": exec_id.to_string(), "url": format!("{}/app/#/executions/{exec_id}", app.cfg.base_url),
                      "error": out.error, "lastNodeExecuted": out.last_node, "mode": mode},
        "workflow": {"id": wf.id, "name": wf.name},
    }));
    let l = Launch { wf: ewf, mode: "error".into(), spec: StartSpec { start_node: Some(trigger), items: Some(vec![item]), destination: None, ..Default::default() }, reply: None, retry_of: None, user: None, exec_id: None };
    if let Err(e) = launch(app.clone(), l) {
        tracing::error!("error workflow failed to start: {e}");
    }
}

/// Continues a parked execution. `items` is the Wait node's output (None = its input, for timeouts).
/// Returns false when the execution is not (or no longer) waiting.
pub fn resume(app: &AppState, exec_id: i64, items: Option<Vec<Item>>) -> Result<bool> {
    let Some(snap) = app.db.take_waiting(exec_id)? else { return Ok(false) };
    let snap: Snapshot = serde_json::from_value(snap)?;
    let ex = app.db.execution(exec_id)?.ok_or_else(|| anyhow!("execution {exec_id} not found"))?;
    let wf: Workflow = serde_json::from_value(ex["workflowData"].clone())?;
    let prev: RunData = serde_json::from_value(ex["data"]["resultData"]["runData"].clone()).unwrap_or_default();
    let items = items.unwrap_or_else(|| snap.wait_input.clone());
    let mode = as_str(&ex["mode"]);
    let user = app.db.owner_of("workflows", &wf.id).ok().flatten();
    let spec = StartSpec { resume: Some((snap, items)), prev_run_data: Some(prev), ..Default::default() };
    launch(app.clone(), Launch { wf, mode, spec, reply: None, retry_of: None, user, exec_id: Some(exec_id) })?;
    Ok(true)
}

/// Fires timed resumes (Wait "time interval"/"specific time" and limited waits).
pub async fn resume_due(app: AppState) {
    loop {
        for id in app.db.due_waiting(now_ms()).unwrap_or_default() {
            if let Err(e) = resume(&app, id, None) {
                tracing::error!("resuming execution {id} failed: {e}");
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}

/// Runs a sub-workflow inline on the current execution thread.
pub async fn run_sub(parent: &Engine, wf: Workflow, items: Vec<Item>) -> Result<Vec<Item>> {
    if parent.depth >= 16 {
        return Err(anyhow!("Sub-workflow nesting is too deep"));
    }
    let app: &App = &parent.app;
    let exec_id = app.db.create_execution(&wf, "integrated", None)?;
    let start = wf
        .nodes
        .iter()
        .find(|n| n.kind == "n8n-nodes-base.executeWorkflowTrigger" && !n.disabled)
        .or_else(|| wf.nodes.iter().find(|n| nodes::is_trigger(&n.kind) && !n.disabled))
        .map(|n| n.name.clone());
    let mut eng = Engine::new(parent.app.clone(), wf, "integrated", exec_id, parent.cancel.clone(), parent.depth + 1)?;
    eng.user = parent.user.clone();
    let out = Box::pin(eng.run(StartSpec { start_node: start, items: Some(items), destination: None, ..Default::default() })).await;
    app.db.finish_execution(exec_id, &out.status, &eng.result_data(&out))?;
    if out.status != "success" {
        return Err(anyhow!("Sub-workflow failed: {}", out.error.as_ref().map(|e| as_str(&e["message"])).unwrap_or_default()));
    }
    let last = out.last_node.unwrap_or_default();
    Ok(eng.run_data.borrow().get(&last).and_then(|r| r.last()).map(|t| t.output(0)).unwrap_or_default())
}
