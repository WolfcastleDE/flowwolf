//! Node registry and shared helpers. Node type names and parameter names match n8n,
//! so exported n8n workflows run unchanged.

mod ai;
pub mod cluster;
mod core;
pub mod data;
pub(crate) mod extra;
mod google;
mod ftp;
mod mongo;
mod sftp;
mod s3;
pub mod declarative;
pub mod integrations;
mod mysql;
mod util;
pub mod vector;

use crate::engine::Engine;
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::sync::OnceLock;

pub use data::{binary_bytes, make_binary, mime_for};
pub use util::{base32_decode, totp_at};
pub use integrations::AcceptAnyCert;

pub type Out = Vec<Vec<Item>>;

static DESCRIPTIONS: OnceLock<Value> = OnceLock::new();

/// Node and credential type descriptions consumed by the editor.
pub fn descriptions() -> &'static Value {
    DESCRIPTIONS.get_or_init(|| {
        let mut base: Value = serde_json::from_str(include_str!("nodes.json")).expect("nodes.json is valid");
        let generated: Value = serde_json::from_str(include_str!("integrations.json")).expect("integrations.json is valid");
        for key in ["nodes", "credentials"] {
            let extra = generated[key].as_array().cloned().unwrap_or_default();
            let list = base[key].as_array_mut().expect("list");
            // Generated definitions replace hand written ones with the same name.
            list.retain(|x| !extra.iter().any(|e| e["name"] == x["name"]));
            list.extend(extra);
        }
        base
    })
}

pub fn description(kind: &str) -> Option<&'static Value> {
    descriptions()["nodes"].as_array()?.iter().find(|n| n["name"] == kind)
}

/// Description default of a top-level parameter of a declarative node. Several properties may share
/// a name (one per operation); the one whose `displayOptions.show` matches the other parameters wins.
/// Also used by webhook-registering triggers. Hand-written nodes are left out on purpose: they carry
/// their n8n defaults in code.
pub fn param_default(node: &Node, name: &str) -> Option<Value> {
    fn find(props: &[Value], params: &Value, name: &str, depth: u8) -> Option<Value> {
        if depth > 3 {
            return None;
        }
        props
            .iter()
            .filter(|p| p["name"] == name)
            .find(|p| {
                p["displayOptions"]["show"].as_object().is_none_or(|show| {
                    show.iter().all(|(k, allowed)| {
                        let v = params.get(k).filter(|v| !v.is_null()).cloned().or_else(|| find(props, params, k, depth + 1));
                        v.is_some_and(|v| allowed.as_array().is_some_and(|a| a.contains(&v)))
                    })
                })
            })
            .map(|p| p["default"].clone())
            .filter(|d| !d.is_null())
    }
    let desc = description(&node.kind).filter(|d| d["declarative"].is_object() || d["webhookTrigger"].is_object())?;
    find(desc["properties"].as_array()?, &node.parameters, name, 0)
}

pub fn is_trigger(kind: &str) -> bool {
    description(kind).is_some_and(|d| d["group"].as_array().is_some_and(|g| g.iter().any(|x| x == "trigger")))
}

/// Number of regular outputs, used to place the error output.
pub fn output_count(node: &Node) -> usize {
    if let Some(n) = cluster::output_count(node) {
        return n;
    }
    match node.kind.as_str() {
        "n8n-nodes-base.if" => 2,
        "n8n-nodes-base.splitInBatches" => 2,
        "n8n-nodes-base.switch" => core::switch_outputs(node),
        "n8n-nodes-base.compareDatasets" => 4,
        _ => 1,
    }
}

pub struct Ctx<'a> {
    pub eng: &'a Engine,
    pub node: &'a Node,
    pub inputs: Vec<Vec<Item>>,
}

impl<'a> Ctx<'a> {
    pub fn items(&self) -> &[Item] {
        self.inputs.first().map(|v| v.as_slice()).unwrap_or(&[])
    }

    pub fn input(&self, i: usize) -> Vec<Item> {
        self.inputs.get(i).cloned().unwrap_or_default()
    }

    /// Resolved parameter; a missing top-level parameter falls back to the default in the node
    /// description (n8n leaves parameters that equal their default out of saved workflows).
    pub fn get(&self, path: &str, i: usize) -> Result<Option<Value>> {
        let fallback;
        let raw = match self.node.raw(path) {
            Some(v) => v,
            None if !path.contains('.') => match param_default(self.node, path) {
                Some(d) => {
                    fallback = d;
                    &fallback
                }
                None => return Ok(None),
            },
            None => return Ok(None),
        };
        self.eng.resolve(raw, i).map(Some).map_err(|e| anyhow!("{e} [parameter '{path}']"))
    }

    pub fn val(&self, path: &str, i: usize, d: Value) -> Result<Value> {
        Ok(self.get(path, i)?.filter(|v| !v.is_null()).unwrap_or(d))
    }

    pub fn s(&self, path: &str, i: usize, d: &str) -> Result<String> {
        Ok(match self.get(path, i)? {
            Some(Value::Null) | None => d.to_string(),
            Some(v) => rl_value(&v),
        })
    }

    pub fn n(&self, path: &str, i: usize, d: f64) -> Result<f64> {
        Ok(self.get(path, i)?.and_then(|v| as_f64(&v)).unwrap_or(d))
    }

    pub fn b(&self, path: &str, i: usize, d: bool) -> Result<bool> {
        Ok(match self.get(path, i)? {
            Some(Value::Bool(b)) => b,
            Some(Value::String(s)) if !s.is_empty() => s == "true",
            _ => d,
        })
    }

    /// Entries of a fixedCollection, e.g. `assignments.assignments`.
    pub fn list(&self, path: &str, i: usize) -> Result<Vec<Value>> {
        Ok(match self.get(path, i)? {
            Some(Value::Array(a)) => a,
            Some(v @ Value::Object(_)) => vec![v],
            _ => vec![],
        })
    }

    /// Decrypted credential data for the given type.
    pub fn creds(&self, kind: &str) -> Result<Value> {
        let id = self.cred_id(kind)?;
        let (_, _, cipher) = self.eng.app.db.credential(&id)?.ok_or_else(|| anyhow!("Credentials not found"))?;
        self.eng.app.cipher.decrypt(&cipher)
    }

    /// Access token of an OAuth2 credential (refreshed when needed).
    pub async fn oauth_token(&self, kind: &str) -> Result<String> {
        crate::oauth::access_token(&self.eng.app, &self.cred_id(kind)?).await
    }

    /// Id of the credential configured for `kind`; falls back to a name match (imports from n8n).
    pub fn cred_id(&self, kind: &str) -> Result<String> {
        let id = self.find_cred(kind)?;
        if let Some(u) = &self.eng.user {
            if !self.eng.app.db.can_access("credentials", &id, u)? {
                return Err(anyhow!("The owner of this workflow has no access to the \"{kind}\" credential set on node \"{}\"", self.node.name));
            }
        }
        Ok(id)
    }

    fn find_cred(&self, kind: &str) -> Result<String> {
        let r = self
            .node
            .credentials
            .as_ref()
            .and_then(|c| c.get(kind))
            .ok_or_else(|| anyhow!("Node \"{}\" has no credentials of type \"{kind}\" set", self.node.name))?;
        let db = &self.eng.app.db;
        let id = r["id"].as_str().filter(|id| db.credential(id).ok().flatten().is_some()).map(String::from);
        match id {
            Some(id) => Ok(id),
            None => db
                .credential_by_name(r["name"].as_str().unwrap_or_default(), kind)?
                .ok_or_else(|| anyhow!("Credentials \"{}\" not found", as_str(&r["name"]))),
        }
    }
}

/// Resource locator values (`{__rl: true, value, mode}`) collapse to their value.
pub fn rl_value(v: &Value) -> String {
    match v {
        Value::Object(m) if m.contains_key("__rl") || m.contains_key("value") && m.contains_key("mode") => as_str(&m["value"]),
        other => as_str(other),
    }
}

pub async fn execute(eng: &Engine, node: &Node, inputs: Vec<Vec<Item>>) -> Result<Out> {
    let ctx = Ctx { eng, node, inputs };
    let kind = node.kind.as_str();
    match kind {
        // ---- triggers: pass the trigger payload through ----
        "n8n-nodes-base.manualTrigger" | "n8n-nodes-base.webhook" | "n8n-nodes-base.formTrigger"
        | "n8n-nodes-base.executeWorkflowTrigger" | "n8n-nodes-base.errorTrigger" | "n8n-nodes-base.start"
        | "@n8n/n8n-nodes-langchain.chatTrigger" | "n8n-nodes-base.emailReadImap" | "n8n-nodes-base.workflowTrigger"
        | "n8n-nodes-base.mqttTrigger" | "n8n-nodes-base.redisTrigger" | "n8n-nodes-base.rssFeedReadTrigger" | "n8n-nodes-base.localFileTrigger" => {
            let items = ctx.input(0);
            Ok(vec![if items.is_empty() { vec![Item::new(json!({}))] } else { items }])
        }
        k if crate::hooktriggers::spec(k).is_some() => Ok(vec![crate::hooktriggers::output(k, ctx.input(0))]),
        k if crate::pollers::spec(k).is_some() => {
            let items = ctx.input(0);
            if !items.is_empty() {
                return Ok(vec![items]);
            }
            let s = crate::pollers::spec(k).expect("checked");
            let since = chrono::Utc::now() - chrono::Duration::days(30);
            let found = crate::pollers::fetch(&eng.app, ctx.node, s, since).await?;
            Ok(vec![found.into_iter().take(20).map(Item::new).collect()])
        }
        "n8n-nodes-base.scheduleTrigger" | "n8n-nodes-base.cron" | "n8n-nodes-base.interval" => {
            let items = ctx.input(0);
            Ok(vec![if items.is_empty() { vec![Item::new(crate::triggers::schedule_item(&eng.app.cfg.timezone))] } else { items }])
        }
        "n8n-nodes-base.noOp" | "n8n-nodes-base.stickyNote" => Ok(vec![ctx.input(0)]),
        _ => {
            if let Some(r) = core::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = data::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = integrations::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = extra::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = util::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = mysql::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = vector::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = ai::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = cluster::run(&ctx, kind).await {
                return r;
            }
            if let Some(r) = declarative::run(&ctx, kind).await {
                return r;
            }
            Err(anyhow!("Unrecognized node type: {kind}"))
        }
    }
}

// ---------------------------------------------------------------------------
// Filter conditions (n8n "filter" parameter type, used by IF v2, Filter v2, Switch v3)
// ---------------------------------------------------------------------------

fn to_date(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => data::parse_date(s).map(|d| d.timestamp_millis()),
        _ => None,
    }
}

fn is_empty(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
        _ => false,
    }
}

fn loose_eq(a: &Value, b: &Value) -> bool {
    match (as_f64(a), as_f64(b)) {
        (Some(x), Some(y)) if !matches!((a, b), (Value::String(_), Value::String(_))) => x == y,
        _ => as_str(a) == as_str(b),
    }
}

/// Evaluates one condition. `t` = operator type, `op` = operation.
pub fn check(t: &str, op: &str, l: &Value, r: &Value, case_sensitive: bool) -> Result<bool> {
    match op {
        "exists" => return Ok(!l.is_null()),
        "notExists" => return Ok(l.is_null()),
        "empty" | "isEmpty" => return Ok(is_empty(l)),
        "notEmpty" | "isNotEmpty" => return Ok(!is_empty(l)),
        _ => {}
    }
    let lc = |v: &Value| if case_sensitive { as_str(v) } else { as_str(v).to_lowercase() };
    Ok(match t {
        "number" => {
            let (a, b) = (as_f64(l), as_f64(r));
            let (Some(a), Some(b)) = (a, b) else {
                return Ok(op == "notEquals" || op == "notEqual");
            };
            match op {
                "equals" | "equal" => a == b,
                "notEquals" | "notEqual" => a != b,
                "gt" | "larger" => a > b,
                "lt" | "smaller" => a < b,
                "gte" | "largerEqual" => a >= b,
                "lte" | "smallerEqual" => a <= b,
                _ => return Err(anyhow!("Unknown number operation: {op}")),
            }
        }
        "dateTime" => {
            let (Some(a), Some(b)) = (to_date(l), to_date(r)) else {
                return Err(anyhow!("Value is not a valid date: {}", as_str(if to_date(l).is_none() { l } else { r })));
            };
            match op {
                "equals" => a == b,
                "notEquals" => a != b,
                "after" => a > b,
                "before" => a < b,
                "afterOrEquals" => a >= b,
                "beforeOrEquals" => a <= b,
                _ => return Err(anyhow!("Unknown date operation: {op}")),
            }
        }
        "boolean" => match op {
            "true" => truthy(l),
            "false" => !truthy(l),
            "equals" | "equal" => truthy(l) == truthy(r),
            "notEquals" | "notEqual" => truthy(l) != truthy(r),
            _ => return Err(anyhow!("Unknown boolean operation: {op}")),
        },
        "array" => {
            let a = l.as_array().cloned().unwrap_or_default();
            let n = as_f64(r).unwrap_or(0.0) as usize;
            match op {
                "contains" => a.iter().any(|x| loose_eq(x, r)),
                "notContains" => !a.iter().any(|x| loose_eq(x, r)),
                "lengthEquals" => a.len() == n,
                "lengthNotEquals" => a.len() != n,
                "lengthGt" => a.len() > n,
                "lengthLt" => a.len() < n,
                "lengthGte" => a.len() >= n,
                "lengthLte" => a.len() <= n,
                _ => return Err(anyhow!("Unknown array operation: {op}")),
            }
        }
        _ => {
            let (a, b) = (lc(l), lc(r));
            match op {
                "equals" | "equal" => a == b,
                "notEquals" | "notEqual" => a != b,
                "contains" => a.contains(&b),
                "notContains" => !a.contains(&b),
                "startsWith" => a.starts_with(&b),
                "notStartsWith" => !a.starts_with(&b),
                "endsWith" => a.ends_with(&b),
                "notEndsWith" => !a.ends_with(&b),
                "regex" | "notRegex" => {
                    let re = regex_lite(&as_str(r), case_sensitive)?;
                    re.is_match(&as_str(l)) == (op == "regex")
                }
                _ => return Err(anyhow!("Unknown string operation: {op}")),
            }
        }
    })
}

/// Evaluates a resolved filter value `{conditions: [...], combinator, options}`.
pub fn eval_filter(f: &Value) -> Result<bool> {
    let conds = f["conditions"].as_array().cloned().unwrap_or_default();
    let case_sensitive = f["options"]["caseSensitive"].as_bool().unwrap_or(true);
    let or = f["combinator"] == "or";
    if conds.is_empty() {
        return Ok(true);
    }
    for c in conds {
        let t = c["operator"]["type"].as_str().unwrap_or("string");
        let op = c["operator"]["operation"].as_str().unwrap_or("equals");
        let ok = check(t, op, &c["leftValue"], &c["rightValue"], case_sensitive)?;
        if or && ok {
            return Ok(true);
        }
        if !or && !ok {
            return Ok(false);
        }
    }
    Ok(!or)
}

/// IF v1 / Filter v1 style conditions: `{string: [{value1, operation, value2}], number: [...], ...}`.
pub fn eval_legacy(c: &Value, any: bool) -> Result<bool> {
    let mut results = vec![];
    for t in ["boolean", "number", "string", "dateTime"] {
        for x in c[t].as_array().into_iter().flatten() {
            let op = x["operation"].as_str().unwrap_or(match t {
                "boolean" | "number" | "string" => "equal",
                _ => "after",
            });
            let op = if op == "smaller" && t == "dateTime" { "before" } else { op };
            results.push(check(t, op, &x["value1"], &x["value2"], true)?);
        }
    }
    Ok(if any { results.iter().any(|b| *b) } else { results.iter().all(|b| *b) })
}

/// Accepts `/pattern/flags` (JavaScript style) or a bare pattern.
fn regex_lite(p: &str, case_sensitive: bool) -> Result<regex::Regex> {
    let (pat, flags) = match p.strip_prefix('/').and_then(|s| s.rsplit_once('/')) {
        Some((pat, flags)) => (pat.to_string(), flags.to_string()),
        None => (p.to_string(), String::new()),
    };
    Ok(regex::RegexBuilder::new(&pat).case_insensitive(flags.contains('i') || !case_sensitive).build()?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filters() {
        let f = json!({"combinator": "and", "options": {"caseSensitive": false}, "conditions": [
            {"leftValue": "Hello World", "rightValue": "world", "operator": {"type": "string", "operation": "contains"}},
            {"leftValue": "5", "rightValue": 3, "operator": {"type": "number", "operation": "gt"}},
            {"leftValue": "2024-02-01", "rightValue": "2024-01-01T00:00:00Z", "operator": {"type": "dateTime", "operation": "after"}},
            {"leftValue": [1, 2], "rightValue": 2, "operator": {"type": "array", "operation": "contains"}},
            {"leftValue": "abc123", "rightValue": "^[a-z]+\\d+$", "operator": {"type": "string", "operation": "regex"}},
        ]});
        assert!(eval_filter(&f).unwrap());
        let legacy = json!({"number": [{"value1": 1, "operation": "larger", "value2": 2}], "string": [{"value1": "a", "value2": "a"}]});
        assert!(!eval_legacy(&legacy, false).unwrap());
        assert!(eval_legacy(&legacy, true).unwrap());
    }
}
