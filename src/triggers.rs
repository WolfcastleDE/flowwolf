//! Activation of workflows: schedules, webhooks and forms.

use crate::app::{AppState, HookTarget};
use crate::engine::{Launch, StartSpec, launch};
use crate::model::*;
use anyhow::{Result, anyhow};
use chrono::{Datelike, Timelike, Utc};
use serde_json::{Value, json};
use std::str::FromStr;
use std::time::Duration;

enum Spec {
    Every(Duration),
    Cron(cron::Schedule),
}

pub fn schedule_item(tz: &str) -> Value {
    let tzv: chrono_tz::Tz = tz.parse().unwrap_or(chrono_tz::UTC);
    let now = Utc::now().with_timezone(&tzv);
    json!({
        "timestamp": now.to_rfc3339_opts(chrono::SecondsFormat::Millis, false),
        "Readable date": now.format("%B %-d, %Y, %-I:%M:%S %p").to_string(),
        "Readable time": now.format("%-I:%M:%S %p").to_string(),
        "Day of week": now.format("%A").to_string(),
        "Year": now.year().to_string(),
        "Month": now.format("%B").to_string(),
        "Day of month": now.format("%d").to_string(),
        "Hour": now.format("%H").to_string(),
        "Minute": now.format("%M").to_string(),
        "Second": now.format("%S").to_string(),
        "Timezone": format!("{tz} (UTC{})", now.format("%:z")),
        "second": now.second(), "minute": now.minute(), "hour": now.hour(),
    })
}

/// Converts a 5 or 6 field crontab expression (0 = Sunday) for the `cron` crate
/// (which wants seconds first and 1 = Sunday or names).
pub fn normalize_cron(expr: &str) -> Result<cron::Schedule> {
    let mut f: Vec<String> = expr.split_whitespace().map(String::from).collect();
    if f.len() == 5 {
        f.insert(0, "0".into());
    }
    if f.len() != 6 {
        return Err(anyhow!("Invalid cron expression: {expr}"));
    }
    const DAYS: [&str; 8] = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
    let dow: String = {
        let mut out = String::new();
        let mut num = String::new();
        for ch in f[5].chars().chain(std::iter::once('\0')) {
            if ch.is_ascii_digit() {
                num.push(ch);
                continue;
            }
            if !num.is_empty() {
                // After '/' it is a step, not a day.
                let is_step = out.ends_with('/');
                let n: usize = num.parse().unwrap_or(0);
                out.push_str(if is_step || n > 7 { &num } else { DAYS[n] });
                num.clear();
            }
            if ch != '\0' {
                out.push(ch);
            }
        }
        out
    };
    f[5] = dow;
    // `cron` needs `?` or `*` semantics as standard cron; both work.
    cron::Schedule::from_str(&f.join(" ")).map_err(|e| anyhow!("Invalid cron expression '{expr}': {e}"))
}

fn specs(node: &Node) -> Result<Vec<Spec>> {
    let p = &node.parameters;
    let mut out = vec![];
    let num = |v: &Value, d: f64| as_f64(v).unwrap_or(d);
    match node.kind.as_str() {
        "n8n-nodes-base.scheduleTrigger" => {
            let rules = p["rule"]["interval"].as_array().cloned().unwrap_or_else(|| vec![json!({"field": "days"})]);
            for r in rules {
                let field = r["field"].as_str().unwrap_or("days");
                let m = num(&r["triggerAtMinute"], 0.0) as u32;
                let h = num(&r["triggerAtHour"], 0.0) as u32;
                out.push(match field {
                    "seconds" => Spec::Every(Duration::from_secs(num(&r["secondsInterval"], 30.0).max(1.0) as u64)),
                    "minutes" => Spec::Every(Duration::from_secs(60 * num(&r["minutesInterval"], 5.0).max(1.0) as u64)),
                    "hours" => Spec::Cron(normalize_cron(&format!("0 {m} */{} * * *", num(&r["hoursInterval"], 1.0).max(1.0)))?),
                    "days" => Spec::Cron(normalize_cron(&format!("0 {m} {h} */{} * *", num(&r["daysInterval"], 1.0).max(1.0)))?),
                    "weeks" => {
                        let days = match &r["triggerAtDay"] {
                            Value::Array(a) if !a.is_empty() => a.iter().map(|d| (num(d, 0.0) as u32).to_string()).collect::<Vec<_>>().join(","),
                            _ => "0".into(),
                        };
                        Spec::Cron(normalize_cron(&format!("0 {m} {h} * * {days}"))?)
                    }
                    "months" => Spec::Cron(normalize_cron(&format!(
                        "0 {m} {h} {} */{} *",
                        num(&r["triggerAtDayOfMonth"], 1.0),
                        num(&r["monthsInterval"], 1.0).max(1.0)
                    ))?),
                    "cronExpression" => Spec::Cron(normalize_cron(r["expression"].as_str().unwrap_or(""))?),
                    other => return Err(anyhow!("Unknown schedule interval: {other}")),
                });
            }
        }
        "n8n-nodes-base.interval" => {
            let n = num(&p["interval"], 1.0).max(1.0);
            let f = match p["unit"].as_str().unwrap_or("seconds") {
                "minutes" => 60.0,
                "hours" => 3600.0,
                _ => 1.0,
            };
            out.push(Spec::Every(Duration::from_secs((n * f) as u64)));
        }
        "n8n-nodes-base.cron" => {
            for t in p["triggerTimes"]["item"].as_array().into_iter().flatten() {
                let (h, m) = (num(&t["hour"], 14.0), num(&t["minute"], 0.0));
                let expr = match t["mode"].as_str().unwrap_or("everyDay") {
                    "everyMinute" => "* * * * *".to_string(),
                    "everyHour" => format!("{m} * * * *"),
                    "everyDay" => format!("{m} {h} * * *"),
                    "everyWeek" => format!("{m} {h} * * {}", num(&t["weekday"], 1.0)),
                    "everyMonth" => format!("{m} {h} {} * *", num(&t["dayOfMonth"], 1.0)),
                    "everyX" => {
                        let v = num(&t["value"], 2.0).max(1.0);
                        if t["unit"] == "minutes" { format!("*/{v} * * * *") } else { format!("0 */{v} * * *") }
                    }
                    _ => as_str(&t["cronExpression"]),
                };
                out.push(Spec::Cron(normalize_cron(&expr)?));
            }
        }
        _ => {}
    }
    Ok(out)
}

/// Production webhook path of a Webhook / Form node, without leading slash.
pub fn hook_path(node: &Node) -> String {
    let p = node.parameters["path"].as_str().unwrap_or("").trim_matches('/').to_string();
    let id = node.webhook_id.clone().unwrap_or_default();
    if p.is_empty() {
        id
    } else if p.contains(':') && !id.is_empty() {
        format!("{id}/{p}")
    } else {
        p
    }
}

pub fn hook_method(node: &Node) -> String {
    if node.kind == "n8n-nodes-base.formTrigger" {
        return "FORM".into();
    }
    if node.kind == "@n8n/n8n-nodes-langchain.chatTrigger" {
        return "CHAT".into();
    }
    if node.kind == "@n8n/n8n-nodes-langchain.mcpTrigger" {
        return "MCP".into();
    }
    if let Some(s) = crate::hooktriggers::spec(&node.kind) {
        return s["method"].as_str().unwrap_or("POST").to_string();
    }
    node.parameters["httpMethod"].as_str().unwrap_or("GET").to_uppercase()
}

pub fn is_hook_node(n: &Node) -> bool {
    !n.disabled
        && (matches!(n.kind.as_str(), "n8n-nodes-base.webhook" | "n8n-nodes-base.formTrigger" | "@n8n/n8n-nodes-langchain.chatTrigger")
            || crate::hooktriggers::spec(&n.kind).is_some()
            || n.kind == "@n8n/n8n-nodes-langchain.mcpTrigger")
}

pub fn deactivate(app: &AppState, id: &str) {
    if let Some(handles) = app.triggers.lock().unwrap().remove(id) {
        handles.into_iter().for_each(|h| h.abort());
    }
    app.webhooks.write().unwrap().retain(|_, t| t.workflow_id != id);
}

pub fn activate(app: &AppState, wf: &Workflow) -> Result<()> {
    deactivate(app, &wf.id);
    let has_trigger = wf.nodes.iter().any(|n| !n.disabled && crate::nodes::is_trigger(&n.kind) && n.kind != "n8n-nodes-base.manualTrigger");
    if !has_trigger {
        return Err(anyhow!("Workflow has no trigger node that can be activated (Schedule, Webhook, Form...)"));
    }
    // Webhooks first so path conflicts fail before any timer starts.
    {
        let mut hooks = app.webhooks.write().unwrap();
        for n in wf.nodes.iter().filter(|n| is_hook_node(n)) {
            let key = (hook_method(n), hook_path(n));
            if let Some(t) = hooks.get(&key).filter(|t| t.workflow_id != wf.id) {
                let other = t.workflow_id.clone();
                hooks.retain(|_, t| t.workflow_id != wf.id);
                return Err(anyhow!("The path '{}' is already used by workflow {other}", key.1));
            }
            hooks.insert(key, HookTarget { workflow_id: wf.id.clone(), node: n.name.clone() });
        }
    }
    let tz_name = wf.setting("timezone").map(as_str).filter(|s| !s.is_empty() && s != "DEFAULT").unwrap_or(app.cfg.timezone.clone());
    let tz: chrono_tz::Tz = tz_name.parse().unwrap_or(chrono_tz::UTC);
    let mut handles = vec![];
    for node in wf.nodes.iter().filter(|n| !n.disabled) {
        for spec in specs(node)? {
            let (app2, wf_id, name, tzn) = (app.clone(), wf.id.clone(), node.name.clone(), tz_name.clone());
            handles.push(tokio::spawn(async move {
                loop {
                    let wait = match &spec {
                        Spec::Every(d) => *d,
                        Spec::Cron(s) => match s.upcoming(tz).next() {
                            Some(t) => (t.with_timezone(&Utc) - Utc::now()).to_std().unwrap_or_default(),
                            None => return,
                        },
                    };
                    tokio::time::sleep(wait).await;
                    let Ok(Some(wf)) = app2.db.workflow(&wf_id) else { return };
                    let item = Item::new(schedule_item(&tzn));
                    let l = Launch { wf, mode: "trigger".into(), spec: StartSpec { start_node: Some(name.clone()), items: Some(vec![item]), destination: None, ..Default::default() }, reply: None, retry_of: None, user: None, exec_id: None };
                    if let Err(e) = launch(app2.clone(), l) {
                        tracing::error!("schedule trigger failed: {e}");
                    }
                    // Cron times are minute aligned; avoid double firing within the same second.
                    if matches!(spec, Spec::Cron(_)) {
                        tokio::time::sleep(Duration::from_millis(1100)).await;
                    }
                }
            }));
        }
    }
    for node in wf.nodes.iter().filter(|n| !n.disabled && n.kind == "n8n-nodes-base.emailReadImap") {
        let creds = node_credential(app, node, "imap")?;
        let (app2, wf_id, node2) = (app.clone(), wf.id.clone(), node.clone());
        let every = Duration::from_secs(as_f64(&node.parameters["options"]["pollIntervalSeconds"]).unwrap_or(60.0).max(2.0) as u64);
        handles.push(tokio::spawn(async move {
            let mut skip = std::collections::HashSet::new();
            loop {
                match crate::imap::poll(&creds, &node2.parameters, &mut skip).await {
                    Ok(mails) => {
                        let Ok(Some(wf)) = app2.db.workflow(&wf_id) else { return };
                        for item in mails {
                            let spec = StartSpec { start_node: Some(node2.name.clone()), items: Some(vec![item]), ..Default::default() };
                            if let Err(e) = launch(app2.clone(), Launch { wf: wf.clone(), mode: "trigger".into(), spec, reply: None, retry_of: None, user: None, exec_id: None }) {
                                tracing::error!("email trigger failed: {e}");
                            }
                        }
                    }
                    Err(e) => tracing::warn!("IMAP poll for \"{}\" failed: {e}", node2.name),
                }
                tokio::time::sleep(every).await;
            }
        }));
    }
    for node in wf.nodes.iter().filter(|n| !n.disabled) {
        let (a, id, n) = (app.clone(), wf.id.clone(), node.clone());
        match node.kind.as_str() {
            "n8n-nodes-base.mqttTrigger" => handles.push(crate::listeners::mqtt(a, id, n, node_credential(app, node, "mqtt")?)),
            "n8n-nodes-base.redisTrigger" => handles.push(crate::listeners::redis(a, id, n, node_credential(app, node, "redis")?)),
            "n8n-nodes-base.rssFeedReadTrigger" => handles.push(crate::listeners::rss(a, id, n)),
            "n8n-nodes-base.localFileTrigger" => handles.push(crate::listeners::local_file(a, id, n)),
            k if crate::pollers::spec(k).is_some() => handles.push(crate::pollers::start(a, id, n)),
            _ => {}
        }
    }
    app.triggers.lock().unwrap().insert(wf.id.clone(), handles);
    Ok(())
}

/// Decrypted credential of a trigger node (by id, falling back to the name).
pub fn node_credential(app: &AppState, node: &Node, kind: &str) -> Result<Value> {
    let r = node.credentials.as_ref().and_then(|c| c.get(kind)).ok_or_else(|| anyhow!("Node \"{}\" has no {kind} credential", node.name))?;
    let id = match r["id"].as_str().filter(|id| app.db.credential(id).ok().flatten().is_some()) {
        Some(id) => id.to_string(),
        None => app.db.credential_by_name(r["name"].as_str().unwrap_or(""), kind)?.ok_or_else(|| anyhow!("Credential for node \"{}\" not found", node.name))?,
    };
    let (_, _, c) = app.db.credential(&id)?.ok_or_else(|| anyhow!("Credential not found"))?;
    app.cipher.decrypt(&c)
}

/// Fires Workflow Trigger nodes ("activate" when switched on, "update" when saved while active).
pub fn fire_workflow_trigger(app: &AppState, wf: &Workflow, event: &str) {
    for node in wf.nodes.iter().filter(|n| !n.disabled && n.kind == "n8n-nodes-base.workflowTrigger") {
        let events: Vec<String> = node.parameters["events"].as_array().into_iter().flatten().map(as_str).collect();
        if !events.iter().any(|e| e == event) {
            continue;
        }
        let label = if event == "activate" { "Workflow activated" } else { "Workflow updated" };
        let item = Item::new(json!({"event": label, "timestamp": now_iso(), "workflow_id": wf.id}));
        let spec = StartSpec { start_node: Some(node.name.clone()), items: Some(vec![item]), ..Default::default() };
        if let Err(e) = launch(app.clone(), Launch { wf: wf.clone(), mode: "trigger".into(), spec, reply: None, retry_of: None, user: None, exec_id: None }) {
            tracing::error!("workflow trigger failed: {e}");
        }
    }
}

/// Finds the registered webhook for a request path, supporting `:param` segments.
pub fn match_hook(app: &AppState, method: &str, path: &str) -> Option<(HookTarget, serde_json::Map<String, Value>)> {
    let hooks = app.webhooks.read().unwrap();
    let path = path.trim_matches('/');
    if let Some(t) = hooks.get(&(method.to_string(), path.to_string())) {
        return Some((t.clone(), Default::default()));
    }
    hooks
        .iter()
        .filter(|((m, p), _)| m == method && p.contains(':'))
        .find_map(|((_, p), t)| match_path(p, path).map(|params| (t.clone(), params)))
}

/// Matches `users/:id` against `users/42` and returns the captured segments.
pub fn match_path(pattern: &str, path: &str) -> Option<serde_json::Map<String, Value>> {
    let (ps, segs): (Vec<&str>, Vec<&str>) = (pattern.split('/').collect(), path.trim_matches('/').split('/').collect());
    if ps.len() != segs.len() {
        return None;
    }
    let mut params = serde_json::Map::new();
    for (a, b) in ps.iter().zip(&segs) {
        match a.strip_prefix(':') {
            Some(name) => {
                params.insert(name.to_string(), json!(b));
            }
            None if a == b => {}
            None => return None,
        }
    }
    Some(params)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cron_conversion() {
        let s = normalize_cron("30 9 * * 1-5").unwrap();
        let next = s.upcoming(chrono_tz::UTC).next().unwrap();
        assert_eq!((next.hour(), next.minute()), (9, 30));
        assert!(!matches!(next.weekday(), chrono::Weekday::Sat | chrono::Weekday::Sun));
        assert!(normalize_cron("*/5 * * * *").is_ok());
        assert!(normalize_cron("0 0 * * 0").is_ok());
        assert!(normalize_cron("nope").is_err());
        assert_eq!(match_path("users/:id", "/users/42/").unwrap()["id"], json!("42"));
        assert!(match_path("users/:id", "teams/42").is_none());
    }
}
