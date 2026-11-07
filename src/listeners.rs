//! Long-running trigger listeners: MQTT, Redis pub/sub, RSS feeds and local files.
//! Each returns a task handle that `triggers::activate` keeps until deactivation.

use crate::app::AppState;
use crate::engine::{Launch, StartSpec, launch};
use crate::model::*;
use anyhow::Result;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::time::Duration;
use tokio::task::JoinHandle;

pub(crate) fn fire(app: &AppState, wf_id: &str, node: &str, items: Vec<Item>) {
    let Ok(Some(wf)) = app.db.workflow(wf_id) else { return };
    let spec = StartSpec { start_node: Some(node.to_string()), items: Some(items), ..Default::default() };
    if let Err(e) = launch(app.clone(), Launch { wf, mode: "trigger".into(), spec, reply: None, retry_of: None, user: None, exec_id: None }) {
        tracing::error!("trigger {node} failed: {e}");
    }
}

fn parse_body(s: &str, json_parse: bool) -> Value {
    if json_parse { serde_json::from_str(s).unwrap_or_else(|_| json!(s)) } else { json!(s) }
}

pub fn mqtt_options(c: &Value, id_suffix: &str) -> rumqttc::MqttOptions {
    let client_id = c["clientId"].as_str().filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| format!("flowwolf-{id_suffix}"));
    let mut o = rumqttc::MqttOptions::new(client_id, as_str(&c["host"]), as_f64(&c["port"]).unwrap_or(1883.0) as u16);
    o.set_keep_alive(Duration::from_secs(30));
    o.set_clean_session(c["clean"].as_bool().unwrap_or(true));
    if !as_str(&c["username"]).is_empty() {
        o.set_credentials(as_str(&c["username"]), as_str(&c["password"]));
    }
    o
}

pub fn qos(v: &Value) -> rumqttc::QoS {
    match as_f64(v).unwrap_or(0.0) as u8 {
        2 => rumqttc::QoS::ExactlyOnce,
        1 => rumqttc::QoS::AtLeastOnce,
        _ => rumqttc::QoS::AtMostOnce,
    }
}

/// MQTT Trigger: subscribes to topics and starts the workflow for each message (reconnects on errors).
pub fn mqtt(app: AppState, wf_id: String, node: Node, creds: Value) -> JoinHandle<()> {
    tokio::spawn(async move {
        let topics: Vec<String> = as_str(&node.parameters["topics"]).split(',').map(|t| t.trim().to_string()).filter(|t| !t.is_empty()).collect();
        let json_parse = node.parameters["options"]["jsonParseBody"].as_bool().unwrap_or(false);
        let only_message = node.parameters["options"]["onlyMessage"].as_bool().unwrap_or(false);
        loop {
            let (client, mut ev) = rumqttc::AsyncClient::new(mqtt_options(&creds, &uuid::Uuid::new_v4().simple().to_string()[..8]), 64);
            for t in &topics {
                let _ = client.subscribe(t, qos(&node.parameters["options"]["qos"])).await;
            }
            loop {
                match ev.poll().await {
                    Ok(rumqttc::Event::Incoming(rumqttc::Packet::Publish(p))) => {
                        let msg = parse_body(&String::from_utf8_lossy(&p.payload), json_parse);
                        let j = if only_message { if msg.is_object() { msg } else { json!({ "message": msg }) } } else { json!({"topic": p.topic, "message": msg}) };
                        fire(&app, &wf_id, &node.name, vec![Item::new(j)]);
                    }
                    Ok(_) => {}
                    Err(e) => {
                        tracing::warn!("MQTT trigger \"{}\": {e}; reconnecting", node.name);
                        break;
                    }
                }
            }
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    })
}

/// Redis Trigger: subscribes to channels (patterns allowed with *).
pub fn redis(app: AppState, wf_id: String, node: Node, creds: Value) -> JoinHandle<()> {
    tokio::spawn(async move {
        use futures::StreamExt;
        let channels: Vec<String> = as_str(&node.parameters["channels"]).split(',').map(|t| t.trim().to_string()).filter(|t| !t.is_empty()).collect();
        let json_parse = node.parameters["options"]["jsonParseBody"].as_bool().unwrap_or(false);
        let only_message = node.parameters["options"]["onlyMessage"].as_bool().unwrap_or(false);
        loop {
            let res: Result<()> = async {
                let pw = as_str(&creds["password"]);
                let auth = if pw.is_empty() { String::new() } else { format!(":{}@", pw) };
                let url = format!("redis://{auth}{}:{}/{}", as_str(&creds["host"]), as_f64(&creds["port"]).unwrap_or(6379.0), as_f64(&creds["database"]).unwrap_or(0.0));
                let mut ps = redis::Client::open(url)?.get_async_pubsub().await?;
                for ch in &channels {
                    if ch.contains('*') { ps.psubscribe(ch).await? } else { ps.subscribe(ch).await? }
                }
                let mut stream = ps.on_message();
                while let Some(m) = stream.next().await {
                    let payload: String = m.get_payload().unwrap_or_default();
                    let msg = parse_body(&payload, json_parse);
                    let j = if only_message { if msg.is_object() { msg } else { json!({ "message": msg }) } } else { json!({"channel": m.get_channel_name(), "message": msg}) };
                    fire(&app, &wf_id, &node.name, vec![Item::new(j)]);
                }
                Ok(())
            }
            .await;
            if let Err(e) = res {
                tracing::warn!("Redis trigger \"{}\": {e}; reconnecting", node.name);
            }
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    })
}

/// RSS Feed Trigger: polls the feed and emits items not seen before (the first poll only records).
pub fn rss(app: AppState, wf_id: String, node: Node) -> JoinHandle<()> {
    tokio::spawn(async move {
        let url = as_str(&node.parameters["feedUrl"]);
        let every = Duration::from_secs(as_f64(&node.parameters["pollIntervalSeconds"]).unwrap_or(300.0).max(2.0) as u64);
        let mut seen: Option<HashSet<String>> = None;
        loop {
            match app.http.get(&url).send().await.and_then(|r| r.error_for_status()) {
                Ok(r) => {
                    let items = crate::nodes::data::parse_feed(&r.text().await.unwrap_or_default());
                    let key = |v: &Value| [&v["guid"], &v["id"], &v["link"], &v["title"]].iter().map(|x| as_str(x)).find(|s| !s.is_empty()).unwrap_or_default();
                    match &mut seen {
                        None => seen = Some(items.iter().map(key).collect()),
                        Some(s) => {
                            let new: Vec<Item> = items.into_iter().filter(|i| s.insert(key(i))).map(Item::new).collect();
                            if !new.is_empty() {
                                fire(&app, &wf_id, &node.name, new);
                            }
                        }
                    }
                }
                Err(e) => tracing::warn!("RSS trigger \"{}\": {e}", node.name),
            }
            tokio::time::sleep(every).await;
        }
    })
}

/// Local File Trigger: watches a folder inside the files directory for new or changed files.
pub fn local_file(app: AppState, wf_id: String, node: Node) -> JoinHandle<()> {
    tokio::spawn(async move {
        let root = std::path::PathBuf::from(&app.cfg.files_dir);
        let rel = as_str(&node.parameters["path"]).trim_start_matches(['/', '\\']).to_string();
        let dir = root.join(&rel);
        let events: Vec<String> = node.parameters["events"].as_array().into_iter().flatten().map(as_str).collect();
        let events = if events.is_empty() { vec!["add".to_string()] } else { events };
        let mut known: Option<HashMap<String, std::time::SystemTime>> = None;
        loop {
            let mut now = HashMap::new();
            // Never follow paths that leave the files directory.
            let inside = std::fs::canonicalize(&dir).ok().zip(std::fs::canonicalize(&root).ok()).is_some_and(|(d, r)| d.starts_with(r));
            if inside {
                for e in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
                    if let (Ok(m), Some(name)) = (e.metadata(), e.file_name().to_str()) {
                        if m.is_file() {
                            now.insert(name.to_string(), m.modified().unwrap_or(std::time::UNIX_EPOCH));
                        }
                    }
                }
            }
            if let Some(prev) = &known {
                let mut items = vec![];
                for (name, t) in &now {
                    let ev = match prev.get(name) {
                        None => "add",
                        Some(old) if old != t => "change",
                        _ => continue,
                    };
                    if events.iter().any(|e| e == ev) {
                        items.push(Item::new(json!({"path": format!("{}/{name}", rel.trim_end_matches('/')).trim_start_matches('/'), "event": ev})));
                    }
                }
                if events.iter().any(|e| e == "unlink") {
                    for name in prev.keys().filter(|n| !now.contains_key(*n)) {
                        items.push(Item::new(json!({"path": format!("{}/{name}", rel.trim_end_matches('/')).trim_start_matches('/'), "event": "unlink"})));
                    }
                }
                if !items.is_empty() {
                    fire(&app, &wf_id, &node.name, items);
                }
            }
            known = Some(now);
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
    })
}
