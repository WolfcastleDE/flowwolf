//! Polling triggers (Gmail, Google Sheets, Airtable, Notion, ...): fetch a list on a schedule and
//! start the workflow with the entries that are new since the last poll, like n8n's polling nodes.
//! Described declaratively under `pollTrigger` in the node description:
//!   credential, baseUrl, auth, headers, bodyType, request {method, path, query, template}, root,
//!   id (template over item fields, e.g. "{id}" or "{id}:{updated}"), since (true when the request
//!   filters by "{since}" / "{sinceUnix}"), transform ("sheetRows"), expand {method, path, query}.
//! State lives in the node's static data: `node:<name>.poll` = {since, seen}.

use crate::app::AppState;
use crate::model::*;
use anyhow::Result;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::task::JoinHandle;

pub fn spec(kind: &str) -> Option<&'static Value> {
    crate::nodes::description(kind)?.get("pollTrigger").filter(|s| s.is_object())
}

/// n8n's `pollTimes` (everyMinute, everyHour, everyDay, everyX with value + unit). Seconds are
/// accepted as an extra unit for tests and very chatty sources.
pub fn interval(node: &Node) -> Duration {
    let item = &node.parameters["pollTimes"]["item"][0];
    let secs = match item["mode"].as_str().unwrap_or("everyMinute") {
        "everyHour" => 3600,
        "everyDay" => 86_400,
        "everyWeek" => 604_800,
        "everyX" => {
            let n = as_f64(&item["value"]).unwrap_or(1.0).max(1.0) as u64;
            n * match item["unit"].as_str().unwrap_or("minutes") {
                "seconds" => 1,
                "hours" => 3600,
                _ => 60,
            }
        }
        _ => 60,
    };
    Duration::from_secs(secs.max(1))
}

fn field(item: &Value, path: &str) -> String {
    match get_path(item, path) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(v) => v.to_string(),
    }
}

/// Identity of an entry, e.g. "{id}" or "{id}:{updated}" (so changed entries count as new).
fn key(s: &Value, item: &Value) -> String {
    let tpl = s["id"].as_str().unwrap_or("{id}");
    let mut out = String::new();
    let mut rest = tpl;
    while let Some(a) = rest.find('{') {
        out.push_str(&rest[..a]);
        let Some(b) = rest[a..].find('}') else { break };
        out.push_str(&field(item, &rest[a + 1..a + b]));
        rest = &rest[a + b + 1..];
    }
    out.push_str(rest);
    out
}

/// Header row + values (Google Sheets) into objects with `row_number`.
fn sheet_rows(v: &Value) -> Vec<Value> {
    let rows = v["values"].as_array().cloned().unwrap_or_default();
    let head: Vec<String> = rows.first().and_then(|r| r.as_array()).map(|r| r.iter().map(as_str).collect()).unwrap_or_default();
    rows.iter()
        .enumerate()
        .skip(1)
        .map(|(i, r)| {
            let mut o = serde_json::Map::new();
            o.insert("row_number".into(), json!(i + 1));
            for (n, h) in head.iter().enumerate() {
                o.insert(h.clone(), r.get(n).cloned().unwrap_or(json!("")));
            }
            Value::Object(o)
        })
        .collect()
}

/// One poll: the entries the source returns for the window starting at `since`.
pub async fn fetch(app: &AppState, node: &Node, s: &Value, since: DateTime<Utc>) -> Result<Vec<Value>> {
    let mut vars = serde_json::Map::new();
    vars.insert("since".into(), json!(since.to_rfc3339_opts(chrono::SecondsFormat::Secs, true)));
    vars.insert("sinceUnix".into(), json!(since.timestamp().to_string()));
    let resp = crate::hooktriggers::request(app, node, s, &s["request"], &vars, "poll the source").await?;
    let mut items = if s["transform"] == "sheetRows" {
        sheet_rows(&resp)
    } else {
        match s["root"].as_str().map(|r| get_path(&resp, r).cloned().unwrap_or(Value::Null)).unwrap_or(resp) {
            Value::Array(a) => a,
            Value::Null => vec![],
            other => vec![other],
        }
    };
    // Some lists only carry ids (Gmail); fetch each entry in full.
    if s["expand"].is_object() {
        let mut full = vec![];
        for it in items.iter().take(50) {
            let mut v = vars.clone();
            if let Some(o) = it.as_object() {
                for (k, x) in o {
                    v.insert(format!("item.{k}"), x.clone());
                }
            }
            full.push(crate::hooktriggers::request(app, node, s, &s["expand"], &v, "load an entry").await?);
        }
        items = full;
    }
    Ok(items)
}

fn state_key(node: &Node) -> String {
    format!("node:{}", node.name)
}

fn save_state(app: &AppState, wf_id: &str, node: &Node, state: Value) {
    if let Ok(Some(wf)) = app.db.workflow(wf_id) {
        let mut data = if wf.static_data.is_object() { wf.static_data } else { json!({}) };
        let k = state_key(node);
        if !data[&k].is_object() {
            data[&k] = json!({});
        }
        data[&k]["poll"] = state;
        let _ = app.db.save_static_data(wf_id, &data);
    }
}

/// Keeps polling while the workflow is active. The first poll only records what exists.
pub fn start(app: AppState, wf_id: String, node: Node) -> JoinHandle<()> {
    tokio::spawn(async move {
        let Some(s) = spec(&node.kind) else { return };
        let every = interval(&node);
        let since_mode = s["since"] == true;
        let stored = app.db.workflow(&wf_id).ok().flatten().map(|w| w.static_data[&state_key(&node)]["poll"].clone()).unwrap_or(Value::Null);
        let mut since: DateTime<Utc> = stored["since"].as_str().and_then(|t| DateTime::parse_from_rfc3339(t).ok()).map(|t| t.with_timezone(&Utc)).unwrap_or_else(Utc::now);
        let mut seen: Vec<String> = stored["seen"].as_array().map(|a| a.iter().map(as_str).collect()).unwrap_or_default();
        let mut first = stored.is_null();
        loop {
            if !first {
                tokio::time::sleep(every).await;
            }
            let started = Utc::now();
            match fetch(&app, &node, s, since).await {
                Ok(items) => {
                    let keys: Vec<String> = items.iter().map(|i| key(s, i)).collect();
                    if !first {
                        let new: Vec<Item> = items.iter().zip(&keys).filter(|(_, k)| !seen.contains(k)).map(|(i, _)| Item::new(i.clone())).collect();
                        if !new.is_empty() {
                            crate::listeners::fire(&app, &wf_id, &node.name, new);
                        }
                    }
                    // With a time filter only the overlap with the last window matters; otherwise remember a window of ids.
                    if since_mode {
                        seen = keys;
                    } else {
                        seen.extend(keys);
                        seen.dedup();
                        let n = seen.len();
                        if n > 2000 {
                            seen.drain(..n - 2000);
                        }
                    }
                    since = started;
                    first = false;
                    save_state(&app, &wf_id, &node, json!({"since": since.to_rfc3339_opts(chrono::SecondsFormat::Secs, true), "seen": seen}));
                }
                Err(e) => {
                    tracing::warn!("poll of \"{}\" failed: {e}", node.name);
                    if first {
                        tokio::time::sleep(every).await;
                    }
                }
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_rows_and_intervals() {
        let s = json!({"id": "{id}:{meta.updated}"});
        assert_eq!(key(&s, &json!({"id": 7, "meta": {"updated": "t1"}})), "7:t1");
        let rows = sheet_rows(&json!({"values": [["name", "age"], ["Ada", "36"], ["Bob"]]}));
        assert_eq!(rows[1], json!({"row_number": 3, "name": "Bob", "age": ""}));
        let n = Node { parameters: json!({"pollTimes": {"item": [{"mode": "everyX", "value": 5, "unit": "seconds"}]}}), ..Default::default() };
        assert_eq!(interval(&n), Duration::from_secs(5));
        assert_eq!(interval(&Node::default()), Duration::from_secs(60));
    }
}
