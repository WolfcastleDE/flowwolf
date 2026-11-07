//! Triggers that register a webhook at the external service when the workflow is activated
//! (GitHub, Stripe, Shopify, ...) and delete it again on deactivation, like n8n's webhook triggers.
//! Each trigger is described declaratively in its node description under `webhookTrigger`:
//!   credential, baseUrl, auth, headers, bodyType, create {method, path, template}, idPath,
//!   delete {method, path, template}, method (incoming), output ("body" | "full").
//! The id returned by the service is kept in the node's static data (`node:<name>.webhookId`).
//! With `signature`, every incoming event must carry a valid signature made with the secret that was
//! generated at registration ("{secret}" in the template), returned by the service (`secretPath`) or
//! received in a handshake (`secretFrom: "handshake"`, Asana). Modes: HMAC-SHA256 (hex/base64,
//! optional prefix, `algorithm: "sha1"` for Help Scout/Webex), "timestamped" (t=..,v1=.. over "t.body", Stripe/Calendly) and "equals".

use crate::app::AppState;
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::time::Duration;

pub fn spec(kind: &str) -> Option<&'static Value> {
    crate::nodes::description(kind)?.get("webhookTrigger").filter(|s| s.is_object())
}

pub fn webhook_url(app: &AppState, node: &Node) -> String {
    format!("{}/webhook/{}", app.cfg.base_url, crate::triggers::hook_path(node))
}

fn static_key(node: &Node) -> String {
    format!("node:{}", node.name)
}

/// Fields of a node credential, or a bearer token for OAuth2 kinds.
async fn credential(app: &AppState, node: &Node, kind: &str) -> Result<(Value, Option<String>)> {
    if kind.is_empty() {
        return Ok((json!({}), None));
    }
    let id = node.credentials.as_ref().and_then(|c| c[kind]["id"].as_str()).ok_or_else(|| anyhow!("Node \"{}\" needs a credential of type {kind}", node.name))?.to_string();
    let (_, _, cipher) = app.db.credential(&id)?.ok_or_else(|| anyhow!("Credential of node \"{}\" not found", node.name))?;
    let data = crate::oauth::with_defaults(kind, &app.cipher.decrypt(&cipher)?);
    let token = if crate::oauth::is_oauth(kind) { Some(crate::oauth::access_token(app, &id).await?) } else { None };
    Ok((data, token))
}

/// Sends the create or delete request described in the spec.
async fn call(app: &AppState, node: &Node, s: &Value, which: &str, hook_id: &str, secret: &str) -> Result<Value> {
    let mut vars = serde_json::Map::new();
    vars.insert("webhookUrl".into(), json!(webhook_url(app, node)));
    vars.insert("webhookId".into(), json!(hook_id));
    vars.insert("nodeWebhookId".into(), json!(node.webhook_id.clone().unwrap_or_default()));
    vars.insert("secret".into(), json!(secret));
    request(app, node, s, &s[which], &vars, which).await
}

/// Sends one request of a trigger spec (`baseUrl`, `auth`, `headers`, `bodyType` from the spec;
/// `method`, `path`, `query`, `template` from `req`). Placeholders resolve from `vars`, then
/// `cred.<field>`, then node parameters (with description defaults). `what` names the request in
/// errors; a 404 on "delete" counts as done.
pub async fn request(app: &AppState, node: &Node, s: &Value, req: &Value, vars: &serde_json::Map<String, Value>, what: &str) -> Result<Value> {
    let which = what;
    let kind = as_str(&s["credential"]);
    let (creds, token) = credential(app, node, &kind).await?;
    let mut get = |k: &str| -> Result<Value> {
        if let Some(v) = vars.get(k) {
            return Ok(v.clone());
        }
        Ok(match k {
            _ => match k.strip_prefix("cred.") {
                Some(c) => json!(as_str(&creds[c]).trim_end_matches('/')),
                // Static parameters; a plain "=value" (no expression) is stored like n8n does.
                // n8n leaves parameters that equal their default out of saved workflows.
                None => match node.parameters.get(k).cloned().or_else(|| crate::nodes::param_default(node, k)).unwrap_or(Value::Null) {
                    Value::String(v) => json!(v.strip_prefix('=').filter(|x| !x.contains("{{")).map(String::from).unwrap_or(v)),
                    v => v,
                },
            },
        })
    };
    let mut text = |k: &str| -> Result<String> {
        let v = get(k)?;
        if v.is_null() || v == "" {
            return Err(anyhow!("Parameter '{k}' is required to register the webhook"));
        }
        Ok(match v {
            Value::String(s) => s,
            other => other.to_string(),
        })
    };
    let base = crate::nodes::declarative::fill(s["baseUrl"].as_str().unwrap_or(""), false, &mut text)?;
    let raw_path = req["path"].as_str().unwrap_or("");
    // A path that is a single placeholder may hold an absolute URL (Calendly and Jira return resource URLs).
    let absolute = raw_path.strip_prefix('{').and_then(|p| p.strip_suffix('}')).and_then(|k| vars.get(k)).and_then(|v| v.as_str()).filter(|v| v.starts_with("http"));
    let target = if let Some(u) = absolute {
        u.to_string()
    } else {
        format!("{base}{}", crate::nodes::declarative::fill(raw_path, true, &mut text)?)
    };
    let target = crate::nodes::declarative::rebase(target);
    let method = reqwest::Method::from_bytes(req["method"].as_str().unwrap_or("POST").as_bytes())?;
    let mut rb = app.http.request(method, &target).timeout(Duration::from_secs(60)).header("accept", "application/json");
    for (k, v) in s["headers"].as_object().into_iter().flatten() {
        rb = rb.header(k, crate::nodes::declarative::fill(&as_str(v), false, &mut text)?);
    }
    for a in s["auth"].as_array().into_iter().flatten().filter_map(|a| a.as_str()) {
        let p: Vec<&str> = a.split(':').collect();
        let c = |k: &str| match k.strip_prefix('=') {
            Some(lit) => lit.to_string(),
            None if k.contains('{') => crate::oauth::fill_fields(k, &creds),
            None => as_str(&creds[k]),
        };
        rb = match p.as_slice() {
            ["bearer", f] => rb.bearer_auth(c(f)),
            ["basic", u, pw] => rb.basic_auth(c(u), Some(c(pw))),
            ["header", name, f] => rb.header(*name, c(f)),
            ["query", name, f] => rb.query(&[(name.to_string(), c(f))]),
            ["oauth2"] => rb.bearer_auth(token.clone().unwrap_or_default()),
            _ => return Err(anyhow!("Unknown auth spec {a}")),
        };
    }
    if let Some(q) = req["query"].as_object() {
        let mut pairs = vec![];
        for (k, v) in q {
            let r = crate::nodes::declarative::render(v, &mut get)?;
            if !(r.is_null() || r == "") {
                pairs.push((k.clone(), match r {
                    Value::String(s) => s,
                    other => other.to_string(),
                }));
            }
        }
        rb = rb.query(&pairs);
    }
    if !req["template"].is_null() {
        let body = crate::nodes::declarative::render(&req["template"], &mut get)?;
        rb = if s["bodyType"] == "form" {
            let mut pairs = vec![];
            crate::nodes::declarative::flatten("", &body, &mut pairs);
            rb.form(&pairs)
        } else {
            rb.json(&body)
        };
    }
    let resp = rb.send().await.map_err(|e| anyhow!("The {which} request failed: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    // Deleting a webhook that is already gone is fine.
    if !status.is_success() && !(which == "delete" && status == reqwest::StatusCode::NOT_FOUND) {
        let subject = if which == "create" || which == "delete" { format!("{which} the webhook") } else { which.to_string() };
        return Err(anyhow!("Could not {subject} ({}): {}", status.as_u16(), text.chars().take(300).collect::<String>()));
    }
    Ok(serde_json::from_str(&text).unwrap_or(Value::Null))
}

/// Registers webhooks for every trigger node that has none yet. Updates `wf.static_data` (caller saves).
pub async fn register(app: &AppState, wf: &mut Workflow) -> Result<()> {
    for node in wf.nodes.iter().filter(|n| !n.disabled) {
        let Some(s) = spec(&node.kind) else { continue };
        let key = static_key(node);
        if !as_str(&wf.static_data[&key]["webhookId"]).is_empty() {
            continue;
        }
        let sig = &s["signature"];
        let mut secret = if sig.is_object() && sig["secretFrom"] != "handshake" { crate::security::random_token() } else { String::new() };
        let resp = call(app, node, s, "create", "", &secret).await?;
        if let Some(p) = s["secretPath"].as_str() {
            secret = get_path(&resp, p).map(as_str).unwrap_or_default();
        }
        if sig["secretFrom"] == "handshake" {
            // The service already called us back during the create request; the handshake stored it.
            secret = app.db.workflow(&wf.id)?.map(|w| as_str(&w.static_data[&key]["secret"])).unwrap_or_default();
        }
        let id = match s["idPath"].as_str() {
            Some(p) => get_path(&resp, p).map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            }),
            // Services without ids (Telegram, Typeform tags) are addressed by the node itself.
            None => Some(node.webhook_id.clone().unwrap_or_else(|| "registered".into())),
        }
        .filter(|s| !s.is_empty())
        .ok_or_else(|| anyhow!("The service did not return a webhook id for \"{}\"", node.name))?;
        if !wf.static_data.is_object() {
            wf.static_data = json!({});
        }
        wf.static_data[&key] = json!({"webhookId": id});
        if !secret.is_empty() {
            wf.static_data[&key]["secret"] = json!(secret);
        }
        tracing::info!("registered webhook for \"{}\" in \"{}\"", node.name, wf.name);
    }
    Ok(())
}

/// Deletes the registered webhooks (errors are logged, deactivation always succeeds).
pub async fn unregister(app: &AppState, wf: &mut Workflow) {
    for node in wf.nodes.clone().iter() {
        let Some(s) = spec(&node.kind) else { continue };
        let key = static_key(node);
        let id = as_str(&wf.static_data[&key]["webhookId"]);
        if id.is_empty() {
            continue;
        }
        if let Err(e) = call(app, node, s, "delete", &id, "").await {
            tracing::warn!("removing the webhook of \"{}\" failed: {e}", node.name);
        }
        if let Some(o) = wf.static_data.as_object_mut() {
            o.remove(&key);
        }
    }
}

fn hmac1(key: &[u8], msg: &[u8]) -> Vec<u8> {
    use hmac::{Hmac, Mac};
    let mut m = Hmac::<sha1::Sha1>::new_from_slice(key).expect("any key length");
    m.update(msg);
    m.finalize().into_bytes().to_vec()
}

fn hmac256(key: &[u8], msg: &[u8]) -> Vec<u8> {
    use hmac::{Hmac, Mac};
    let mut m = Hmac::<sha2::Sha256>::new_from_slice(key).expect("any key length");
    m.update(msg);
    m.finalize().into_bytes().to_vec()
}

/// Compares in constant time (signatures must not leak through timing).
fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Checks the signature of an incoming event. Ok when the trigger has no signature scheme or was
/// registered before secrets were stored.
pub fn verify(wf: &Workflow, node: &Node, headers: &axum::http::HeaderMap, body: &[u8]) -> Result<(), String> {
    use base64::Engine as _;
    let Some(sig) = spec(&node.kind).map(|s| &s["signature"]).filter(|s| s.is_object()) else { return Ok(()) };
    let secret = as_str(&wf.static_data[&static_key(node)]["secret"]);
    if secret.is_empty() {
        return Ok(());
    }
    let header = sig["header"].as_str().unwrap_or_default();
    let got = headers.get(header).and_then(|v| v.to_str().ok()).ok_or_else(|| format!("Missing signature header {header}"))?;
    let ok = match sig["mode"].as_str().unwrap_or("hmac") {
        "equals" => same(got, &secret),
        "timestamped" => {
            let parts: Vec<(&str, &str)> = got.split(',').filter_map(|p| p.trim().split_once('=')).collect();
            let t = parts.iter().find(|(k, _)| *k == "t").map(|(_, v)| *v).unwrap_or_default();
            let fresh = t.parse::<i64>().is_ok_and(|t| (chrono::Utc::now().timestamp() - t).abs() <= 300);
            let mut msg = format!("{t}.").into_bytes();
            msg.extend_from_slice(body);
            let expected = hex::encode(hmac256(secret.as_bytes(), &msg));
            fresh && parts.iter().any(|(k, v)| *k == "v1" && same(v, &expected))
        }
        _ => {
            // Help Scout and Webex still sign with SHA-1.
            let mac = if sig["algorithm"] == "sha1" { hmac1(secret.as_bytes(), body) } else { hmac256(secret.as_bytes(), body) };
            let enc = if sig["encoding"] == "base64" { base64::engine::general_purpose::STANDARD.encode(mac) } else { hex::encode(mac) };
            same(got, &format!("{}{enc}", sig["prefix"].as_str().unwrap_or("")))
        }
    };
    if ok { Ok(()) } else { Err("Invalid webhook signature".into()) }
}

/// Fingerprint of everything that decides a registration (to re-register only on real changes).
pub fn fingerprint(wf: &Workflow) -> String {
    wf.nodes.iter().filter(|n| spec(&n.kind).is_some() && !n.disabled).map(|n| format!("{}{}{:?}", n.name, n.parameters, n.credentials)).collect()
}

/// Turns the raw request item into what the trigger emits (the event body by default).
pub fn output(kind: &str, items: Vec<Item>) -> Vec<Item> {
    let full = spec(kind).is_some_and(|s| s["output"] == "full");
    items
        .into_iter()
        .map(|mut it| {
            if !full && (it.json["body"].is_object() || it.json["body"].is_array()) {
                it.json = it.json["body"].take();
            }
            it
        })
        .collect()
}
