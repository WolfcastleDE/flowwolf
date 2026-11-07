//! Core data types. Field names follow the n8n JSON format so workflows can be
//! imported from and exported to n8n without conversion.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::HashMap;

fn one() -> f64 {
    1.0
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct Node {
    #[serde(default)]
    pub id: String,
    pub name: String,
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default = "one")]
    pub type_version: f64,
    #[serde(default)]
    pub position: Value,
    #[serde(default)]
    pub parameters: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub credentials: Option<Value>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub disabled: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub always_output_data: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub execute_once: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub retry_on_fail: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_tries: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wait_between_tries: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub on_error: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub continue_on_fail: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub webhook_id: Option<String>,
    /// Anything we do not model explicitly (notes, color, ...) survives a round trip.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

impl Node {
    /// Plain (unresolved) parameter by dotted path, e.g. "options.timeout".
    pub fn raw(&self, path: &str) -> Option<&Value> {
        get_path(&self.parameters, path)
    }

    pub fn on_error_mode(&self) -> &str {
        match self.on_error.as_deref() {
            Some(m) => m,
            None if self.continue_on_fail => "continueRegularOutput",
            None => "stopWorkflow",
        }
    }
}

pub fn get_path<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    let mut cur = v;
    for part in path.split('.').filter(|p| !p.is_empty()) {
        cur = match cur {
            Value::Object(m) => m.get(part)?,
            Value::Array(a) => a.get(part.parse::<usize>().ok()?)?,
            _ => return None,
        };
    }
    Some(cur)
}

/// Writes `val` at a dotted path, creating intermediate objects.
pub fn set_path(target: &mut Value, path: &str, val: Value) {
    let parts: Vec<&str> = path.split('.').filter(|p| !p.is_empty()).collect();
    let mut cur = target;
    for (i, part) in parts.iter().enumerate() {
        if !cur.is_object() {
            *cur = Value::Object(Map::new());
        }
        let m = cur.as_object_mut().unwrap();
        if i == parts.len() - 1 {
            m.insert(part.to_string(), val);
            return;
        }
        cur = m.entry(part.to_string()).or_insert_with(|| Value::Object(Map::new()));
    }
}

pub fn remove_path(target: &mut Value, path: &str) {
    let (parent, last) = match path.rsplit_once('.') {
        Some((p, l)) => (p, l),
        None => ("", path),
    };
    let parent = if parent.is_empty() {
        Some(target)
    } else {
        let mut cur = Some(target);
        for part in parent.split('.') {
            cur = cur.and_then(|c| c.get_mut(part));
        }
        cur
    };
    if let Some(Value::Object(m)) = parent {
        m.remove(last);
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Conn {
    pub node: String,
    #[serde(rename = "type", default = "main")]
    pub kind: String,
    #[serde(default)]
    pub index: usize,
}

fn main() -> String {
    "main".into()
}

/// source node -> connection type -> output index -> targets
pub type Connections = HashMap<String, HashMap<String, Vec<Option<Vec<Conn>>>>>;

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct Workflow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub nodes: Vec<Node>,
    #[serde(default)]
    pub connections: Value,
    #[serde(default)]
    pub settings: Value,
    #[serde(default)]
    pub static_data: Value,
    #[serde(default)]
    pub pin_data: Value,
    #[serde(default)]
    pub tags: Vec<Value>,
    /// Folder path such as "Sales/Leads"; empty = top level.
    #[serde(default)]
    pub folder: String,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
}

impl Workflow {
    pub fn node(&self, name: &str) -> Option<&Node> {
        self.nodes.iter().find(|n| n.name == name)
    }

    pub fn conns(&self) -> Connections {
        serde_json::from_value(self.connections.clone()).unwrap_or_default()
    }

    pub fn setting(&self, key: &str) -> Option<&Value> {
        self.settings.get(key).filter(|v| !v.is_null())
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct Item {
    #[serde(default)]
    pub json: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binary: Option<Map<String, Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paired_item: Option<Value>,
}

impl Item {
    pub fn new(json: Value) -> Self {
        Item { json, ..Default::default() }
    }
    pub fn paired(json: Value, i: usize) -> Self {
        Item { json, paired_item: Some(serde_json::json!({ "item": i })), binary: None }
    }
}

/// Wraps user supplied JSON (object or `{json: ...}`) into an item.
pub fn to_item(v: Value) -> Item {
    match v {
        Value::Object(ref m) if m.contains_key("json") && m.get("json").is_some_and(|j| j.is_object()) => {
            serde_json::from_value(v.clone()).unwrap_or_else(|_| Item::new(v))
        }
        Value::Object(_) => Item::new(v),
        other => Item::new(serde_json::json!({ "data": other })),
    }
}

pub fn to_items(v: Value) -> Vec<Item> {
    match v {
        Value::Array(a) => a.into_iter().map(to_item).collect(),
        Value::Null => vec![],
        other => vec![to_item(other)],
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct TaskData {
    pub start_time: i64,
    pub execution_time: i64,
    pub execution_status: String,
    pub data: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
    #[serde(default)]
    pub source: Vec<Value>,
}

impl TaskData {
    pub fn output(&self, branch: usize) -> Vec<Item> {
        self.data
            .get("main")
            .and_then(|m| m.get(branch))
            .and_then(|b| serde_json::from_value(b.clone()).ok())
            .unwrap_or_default()
    }
}

pub type RunData = HashMap<String, Vec<TaskData>>;

pub fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

pub fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

/// Value -> string the way JavaScript template literals would print it.
pub fn as_str(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

pub fn as_f64(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty() && s != "false",
        _ => true,
    }
}

pub fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn paths() {
        let mut v = json!({});
        set_path(&mut v, "a.b.c", json!(1));
        assert_eq!(get_path(&v, "a.b.c"), Some(&json!(1)));
        remove_path(&mut v, "a.b.c");
        assert_eq!(v, json!({"a": {"b": {}}}));
        assert_eq!(to_items(json!([{"a": 1}, {"json": {"b": 2}}]))[1].json, json!({"b": 2}));
    }
}
