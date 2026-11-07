//! Gmail and Google Drive operations that need more than one request or build MIME / multipart
//! bodies. Every other operation of both nodes runs from the generated (declarative) definition.

use super::data::{binary_bytes, make_binary, mime_for};
use super::declarative::rebase;
use super::integrations::generated;
use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use base64::engine::general_purpose::{STANDARD, URL_SAFE, URL_SAFE_NO_PAD};
use serde_json::{Map, Value, json};
use std::time::Duration;

/// Sends an authorised Google API request and returns the parsed JSON answer.
async fn call(ctx: &Ctx<'_>, token: &str, rb: reqwest::RequestBuilder) -> Result<Value> {
    let resp = rb.bearer_auth(token).timeout(Duration::from_secs(300)).send().await?;
    let status = resp.status();
    let text = resp.text().await?;
    let v: Value = if text.trim().is_empty() { json!({"success": true}) } else { serde_json::from_str(&text).unwrap_or(json!({"data": text})) };
    if !status.is_success() {
        let msg = v["error"]["message"].as_str().map(String::from).unwrap_or(text);
        return Err(anyhow!("{} returned {}: {msg}", ctx.node.kind.rsplit('.').next().unwrap_or("Google"), status.as_u16()));
    }
    Ok(v)
}

fn opt(v: &Value, k: &str) -> String {
    match &v[k] {
        Value::Null => String::new(),
        x => as_str(x),
    }
}

/// Seconds since the epoch of a dateTime value (RFC 3339 or "YYYY-MM-DDTHH:MM:SS" as UTC).
fn epoch(v: &Value) -> Option<i64> {
    if let Some(n) = as_f64(v) {
        return Some(n as i64);
    }
    let s = as_str(v);
    chrono::DateTime::parse_from_rfc3339(&s).map(|d| d.timestamp()).ok()
        .or_else(|| chrono::NaiveDateTime::parse_from_str(&s, "%Y-%m-%dT%H:%M:%S").ok().map(|d| d.and_utc().timestamp()))
        .or_else(|| chrono::NaiveDate::parse_from_str(&s, "%Y-%m-%d").ok().and_then(|d| d.and_hms_opt(0, 0, 0)).map(|d| d.and_utc().timestamp()))
}

// ---------------------------------------------------------------------------
// Gmail
// ---------------------------------------------------------------------------

fn encode_header(s: &str) -> String {
    if s.is_ascii() { s.to_string() } else { format!("=?UTF-8?B?{}?=", STANDARD.encode(s)) }
}

/// Binary attachments named in `attachmentsUi` (n8n: {attachmentsBinary: [{property}]}, or a comma list).
fn attachments(ctx: &Ctx, i: usize, ui: &Value) -> Result<Vec<(String, String, Vec<u8>)>> {
    let props: Vec<String> = match ui {
        Value::Object(_) => ui["attachmentsBinary"].as_array().into_iter().flatten().map(|a| as_str(&a["property"])).collect(),
        v => vec![as_str(v)],
    };
    let mut out = vec![];
    for p in props.iter().flat_map(|p| p.split(',')).map(str::trim).filter(|p| !p.is_empty()) {
        let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(p)).ok_or_else(|| anyhow!("No binary property \"{p}\" to attach"))?;
        let name = bin["fileName"].as_str().filter(|n| !n.is_empty()).unwrap_or(p).to_string();
        let mime = bin["mimeType"].as_str().filter(|m| !m.is_empty()).map(String::from).unwrap_or_else(|| mime_for(&name).to_string());
        out.push((name, mime, binary_bytes(bin)?));
    }
    Ok(out)
}

/// Builds an RFC 2822 message and returns it base64url encoded (Gmail's `raw`).
fn mime(headers: &[(&str, String)], html: bool, body: &str, files: &[(String, String, Vec<u8>)]) -> String {
    let mut m = String::new();
    for (k, v) in headers.iter().filter(|(_, v)| !v.is_empty()) {
        m.push_str(&format!("{k}: {v}\r\n"));
    }
    m.push_str("MIME-Version: 1.0\r\n");
    let text_part = format!("Content-Type: {}; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n{}\r\n",
                            if html { "text/html" } else { "text/plain" }, STANDARD.encode(body));
    if files.is_empty() {
        m.push_str(&text_part);
    } else {
        let b = format!("flowwolf_{}", uuid::Uuid::new_v4().simple());
        m.push_str(&format!("Content-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n--{b}\r\n{text_part}"));
        for (name, ct, data) in files {
            m.push_str(&format!("--{b}\r\nContent-Type: {ct}; name=\"{name}\"\r\nContent-Disposition: attachment; filename=\"{name}\"\r\nContent-Transfer-Encoding: base64\r\n\r\n{}\r\n",
                                STANDARD.encode(data)));
        }
        m.push_str(&format!("--{b}--\r\n"));
    }
    URL_SAFE_NO_PAD.encode(m)
}

fn header_of(msg: &Value, name: &str) -> String {
    msg["payload"]["headers"].as_array().into_iter().flatten()
        .find(|h| as_str(&h["name"]).eq_ignore_ascii_case(name)).map(|h| as_str(&h["value"])).unwrap_or_default()
}

fn b64url(s: &str) -> Vec<u8> {
    URL_SAFE.decode(s.trim_end_matches('=').to_string() + &"=".repeat((4 - s.trim_end_matches('=').len() % 4) % 4)).unwrap_or_default()
}

/// Flattens a `format=metadata` message into n8n's simplified shape.
fn simplified(msg: &Value) -> Value {
    let mut o = Map::new();
    for k in ["id", "threadId", "labelIds", "snippet", "sizeEstimate", "historyId", "internalDate"] {
        if !msg[k].is_null() {
            o.insert(k.into(), msg[k].clone());
        }
    }
    for h in msg["payload"]["headers"].as_array().into_iter().flatten() {
        o.insert(as_str(&h["name"]), h["value"].clone());
    }
    Value::Object(o)
}

/// Turns a `format=full` message into the parsed shape (text, html, headers, addresses) and
/// collects attachments when asked to.
async fn parsed(ctx: &Ctx<'_>, token: &str, base: &str, msg: &Value, download: bool, prefix: &str) -> Result<Item> {
    let (mut text, mut html, mut files) = (String::new(), String::new(), vec![]);
    let mut stack = vec![msg["payload"].clone()];
    while let Some(p) = stack.pop() {
        if let Some(parts) = p["parts"].as_array() {
            stack.extend(parts.iter().rev().cloned());
            continue;
        }
        let ct = as_str(&p["mimeType"]);
        let name = as_str(&p["filename"]);
        if !name.is_empty() {
            if download {
                let data = match p["body"]["data"].as_str() {
                    Some(d) => b64url(d),
                    None => {
                        let att = call(ctx, token, ctx.eng.app.http.get(format!("{base}/messages/{}/attachments/{}", as_str(&msg["id"]), as_str(&p["body"]["attachmentId"])))).await?;
                        b64url(att["data"].as_str().unwrap_or(""))
                    }
                };
                files.push((name, ct, data));
            }
        } else if ct == "text/plain" && text.is_empty() {
            text = String::from_utf8_lossy(&b64url(p["body"]["data"].as_str().unwrap_or(""))).into_owned();
        } else if ct == "text/html" && html.is_empty() {
            html = String::from_utf8_lossy(&b64url(p["body"]["data"].as_str().unwrap_or(""))).into_owned();
        }
    }
    let headers: Map<String, Value> = msg["payload"]["headers"].as_array().into_iter().flatten()
        .map(|h| (as_str(&h["name"]).to_lowercase(), h["value"].clone())).collect();
    let addr = |k: &str| { let v = header_of(msg, k); if v.is_empty() { Value::Null } else { json!({"text": v}) } };
    let json = json!({
        "id": msg["id"], "threadId": msg["threadId"], "labelIds": msg["labelIds"], "sizeEstimate": msg["sizeEstimate"],
        "headers": headers, "html": if html.is_empty() { Value::Bool(false) } else { json!(html) }, "text": text,
        "subject": header_of(msg, "Subject"), "date": header_of(msg, "Date"), "messageId": header_of(msg, "Message-ID"),
        "from": addr("From"), "to": addr("To"), "cc": addr("Cc"), "replyTo": addr("Reply-To"),
    });
    let mut item = Item { json, binary: None, paired_item: None };
    if !files.is_empty() {
        let mut bin = Map::new();
        for (n, (name, ct, data)) in files.iter().enumerate() {
            bin.insert(format!("{prefix}{n}"), make_binary(data, ct, name));
        }
        item.binary = Some(bin);
    }
    Ok(item)
}

/// Gmail search string from the n8n filter collection.
fn gmail_query(f: &Value) -> String {
    let mut q = vec![opt(f, "q")];
    match f["readStatus"].as_str() {
        Some("unread") => q.push("is:unread".into()),
        Some("read") => q.push("is:read".into()),
        _ => {}
    }
    if !opt(f, "sender").is_empty() {
        q.push(format!("from:{}", opt(f, "sender")));
    }
    if let Some(t) = epoch(&f["receivedAfter"]).filter(|_| !f["receivedAfter"].is_null()) {
        q.push(format!("after:{t}"));
    }
    if let Some(t) = epoch(&f["receivedBefore"]).filter(|_| !f["receivedBefore"].is_null()) {
        q.push(format!("before:{t}"));
    }
    q.retain(|s| !s.is_empty());
    q.join(" ")
}

/// Lists ids (messages, threads or drafts) page by page up to the limit.
async fn list_all(ctx: &Ctx<'_>, token: &str, url: &str, root: &str, query: Vec<(String, String)>, limit: Option<usize>) -> Result<Vec<Value>> {
    let mut out = vec![];
    let mut page: Option<String> = None;
    loop {
        let mut q = query.clone();
        q.push(("maxResults".into(), limit.map(|l| (l - out.len()).min(500)).unwrap_or(500).to_string()));
        if let Some(p) = &page {
            q.push(("pageToken".into(), p.clone()));
        }
        let v = call(ctx, token, ctx.eng.app.http.get(url).query(&q)).await?;
        out.extend(v[root].as_array().cloned().unwrap_or_default());
        page = v["nextPageToken"].as_str().map(String::from);
        if page.is_none() || limit.is_some_and(|l| out.len() >= l) {
            break;
        }
    }
    if let Some(l) = limit {
        out.truncate(l);
    }
    Ok(out)
}

fn limit_of(ctx: &Ctx, i: usize) -> Result<Option<usize>> {
    Ok(if ctx.b("returnAll", i, false)? { None } else { Some(ctx.n("limit", i, 50.0)? as usize) })
}

pub async fn gmail(ctx: &Ctx<'_>) -> Result<Out> {
    let (resource, op) = (ctx.s("resource", 0, "message")?, ctx.s("operation", 0, "send")?);
    let own = matches!((resource.as_str(), op.as_str()),
        ("message", "send" | "reply" | "get" | "getAll") | ("draft", "create" | "get" | "getAll") | ("thread", "reply" | "get" | "getAll"));
    if !own {
        return generated(ctx).await;
    }
    let token = ctx.oauth_token("gmailOAuth2").await?;
    let base = rebase("https://gmail.googleapis.com/gmail/v1/users/me".into());
    let http = &ctx.eng.app.http;
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let o = ctx.val("options", i, json!({}))?;
        let html = ctx.s("emailType", i, "html")? == "html";
        let body = ctx.s("message", i, "")?;
        let from = if opt(&o, "senderName").is_empty() {
            String::new()
        } else {
            let me = call(ctx, &token, http.get(format!("{base}/profile"))).await?;
            format!("{} <{}>", encode_header(&opt(&o, "senderName")), as_str(&me["emailAddress"]))
        };
        match (resource.as_str(), op.as_str()) {
            ("message", "send") | ("draft", "create") => {
                let to = if resource == "message" { ctx.s("sendTo", i, "")? } else { opt(&o, "sendTo") };
                let from = if !opt(&o, "fromAlias").is_empty() { opt(&o, "fromAlias") } else { from };
                let raw = mime(&[("From", from), ("To", to), ("Cc", opt(&o, "ccList")), ("Bcc", opt(&o, "bccList")), ("Reply-To", opt(&o, "replyTo")),
                                 ("Subject", encode_header(&ctx.s("subject", i, "")?))], html, &body, &attachments(ctx, i, &o["attachmentsUi"])?);
                let v = if resource == "message" {
                    call(ctx, &token, http.post(format!("{base}/messages/send")).json(&json!({"raw": raw}))).await?
                } else {
                    let mut m = json!({"raw": raw});
                    if !opt(&o, "threadId").is_empty() {
                        m["threadId"] = json!(opt(&o, "threadId"));
                    }
                    call(ctx, &token, http.post(format!("{base}/drafts")).json(&json!({"message": m}))).await?
                };
                out.push(Item::paired(v, i));
            }
            ("message", "reply") | ("thread", "reply") => {
                // Reply to the given message, or for threads to the chosen / last message of the thread.
                let original = if resource == "message" {
                    call(ctx, &token, http.get(format!("{base}/messages/{}", ctx.s("messageId", i, "")?)).query(&[("format", "metadata")])).await?
                } else {
                    let t = call(ctx, &token, http.get(format!("{base}/threads/{}", ctx.s("threadId", i, "")?)).query(&[("format", "metadata")])).await?;
                    let msgs = t["messages"].as_array().cloned().unwrap_or_default();
                    let wanted = opt(&o, "messageId");
                    msgs.iter().find(|m| !wanted.is_empty() && m["id"] == json!(wanted)).or(msgs.last()).cloned().ok_or_else(|| anyhow!("Thread has no messages"))?
                };
                let subject = header_of(&original, "Subject");
                let subject = if subject.to_lowercase().starts_with("re:") { subject } else { format!("Re: {subject}") };
                let msg_id = header_of(&original, "Message-ID");
                let refs = [header_of(&original, "References"), msg_id.clone()].iter().filter(|s| !s.is_empty()).cloned().collect::<Vec<_>>().join(" ");
                let sender = { let r = header_of(&original, "Reply-To"); if r.is_empty() { header_of(&original, "From") } else { r } };
                let to = if o["replyToSenderOnly"] == true { sender } else { [sender, header_of(&original, "To")].iter().filter(|s| !s.is_empty()).cloned().collect::<Vec<_>>().join(", ") };
                let raw = mime(&[("From", from), ("To", to), ("Cc", opt(&o, "ccList")), ("Bcc", opt(&o, "bccList")), ("Subject", encode_header(&subject)),
                                 ("In-Reply-To", msg_id), ("References", refs)], html, &body, &attachments(ctx, i, &o["attachmentsUi"])?);
                let v = call(ctx, &token, http.post(format!("{base}/messages/send")).json(&json!({"raw": raw, "threadId": original["threadId"]}))).await?;
                out.push(Item::paired(v, i));
            }
            ("message", "get") | ("message", "getAll") | ("draft", "get") | ("draft", "getAll") => {
                let simple = resource == "message" && ctx.b("simple", i, true)?;
                let download = o["downloadAttachments"] == true;
                let prefix = o["dataPropertyAttachmentsPrefixName"].as_str().unwrap_or("attachment_").to_string();
                let ids: Vec<Value> = match op.as_str() {
                    "get" => vec![json!({"id": ctx.s("messageId", i, "")?})],
                    _ if resource == "message" => {
                        let f = ctx.val("filters", i, json!({}))?;
                        let mut q = vec![];
                        let query = gmail_query(&f);
                        if !query.is_empty() {
                            q.push(("q".to_string(), query));
                        }
                        let labels = match &f["labelIds"] { Value::Array(a) => a.iter().map(as_str).collect(), v => as_str(v).split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect::<Vec<_>>() };
                        q.extend(labels.into_iter().map(|l| ("labelIds".to_string(), l)));
                        if f["includeSpamTrash"] == true {
                            q.push(("includeSpamTrash".into(), "true".into()));
                        }
                        list_all(ctx, &token, &format!("{base}/messages"), "messages", q, limit_of(ctx, i)?).await?
                    }
                    _ => {
                        let q = if o["includeSpamTrash"] == true { vec![("includeSpamTrash".to_string(), "true".to_string())] } else { vec![] };
                        list_all(ctx, &token, &format!("{base}/drafts"), "drafts", q, limit_of(ctx, i)?).await?
                    }
                };
                for id in ids {
                    let id = as_str(&id["id"]);
                    if resource == "draft" {
                        let d = call(ctx, &token, http.get(format!("{base}/drafts/{id}")).query(&[("format", "full")])).await?;
                        let mut item = parsed(ctx, &token, &base, &d["message"], download, &prefix).await?;
                        item.json["messageId"] = d["message"]["id"].clone();
                        item.json["id"] = d["id"].clone();
                        item.paired_item = Some(json!({"item": i}));
                        out.push(item);
                    } else if simple {
                        let m = call(ctx, &token, http.get(format!("{base}/messages/{id}")).query(&[("format", "metadata")])).await?;
                        out.push(Item::paired(simplified(&m), i));
                    } else {
                        let m = call(ctx, &token, http.get(format!("{base}/messages/{id}")).query(&[("format", "full")])).await?;
                        let mut item = parsed(ctx, &token, &base, &m, download, &prefix).await?;
                        item.paired_item = Some(json!({"item": i}));
                        out.push(item);
                    }
                }
            }
            ("thread", "get") => {
                let simple = ctx.b("simple", i, true)?;
                let t = call(ctx, &token, http.get(format!("{base}/threads/{}", ctx.s("threadId", i, "")?)).query(&[("format", if simple { "metadata" } else { "full" })])).await?;
                let msgs: Vec<Value> = t["messages"].as_array().into_iter().flatten().map(|m| if simple { simplified(m) } else { m.clone() }).collect();
                if o["returnOnlyMessages"] == false {
                    out.push(Item::paired(json!({"id": t["id"], "historyId": t["historyId"], "messages": msgs}), i));
                } else {
                    out.extend(msgs.into_iter().map(|m| Item::paired(m, i)));
                }
            }
            ("thread", "getAll") => {
                let f = ctx.val("filters", i, json!({}))?;
                let mut q = vec![];
                let query = gmail_query(&f);
                if !query.is_empty() {
                    q.push(("q".to_string(), query));
                }
                if f["includeSpamTrash"] == true {
                    q.push(("includeSpamTrash".into(), "true".into()));
                }
                for t in list_all(ctx, &token, &format!("{base}/threads"), "threads", q, limit_of(ctx, i)?).await? {
                    out.push(Item::paired(t, i));
                }
            }
            _ => unreachable!(),
        }
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Google Drive
// ---------------------------------------------------------------------------

const FOLDER: &str = "application/vnd.google-apps.folder";

/// File, folder or drive id from a resource locator value: an id, or a Drive / Docs URL.
fn drive_id(v: &str) -> String {
    let v = v.trim();
    if !v.starts_with("http") {
        return v.to_string();
    }
    for marker in ["/d/", "/folders/", "/drives/", "id="] {
        if let Some(rest) = v.split(marker).nth(1) {
            return rest.split(['/', '?', '&', '#']).next().unwrap_or("").to_string();
        }
    }
    v.to_string()
}

/// The parent for new files: the folder, else the shared drive, else My Drive.
fn parent(ctx: &Ctx, i: usize) -> Result<String> {
    let folder = drive_id(&ctx.s("folderId", i, "root")?);
    let drive = drive_id(&ctx.s("driveId", i, "My Drive")?);
    Ok(if !folder.is_empty() && folder != "root" { folder } else if !drive.is_empty() && drive != "My Drive" { drive } else { "root".into() })
}

/// Multipart/related upload of metadata plus content.
async fn upload(ctx: &Ctx<'_>, token: &str, method: reqwest::Method, path: &str, meta: &Value, mime: &str, data: Vec<u8>, query: &[(&str, String)]) -> Result<Value> {
    let b = format!("flowwolf{}", uuid::Uuid::new_v4().simple());
    let mut body = format!("--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{b}\r\nContent-Type: {mime}\r\n\r\n").into_bytes();
    body.extend(data);
    body.extend(format!("\r\n--{b}--").into_bytes());
    let url = rebase(format!("https://www.googleapis.com/upload/drive/v3/files{path}"));
    let mut q: Vec<(&str, String)> = vec![("uploadType", "multipart".into()), ("supportsAllDrives", "true".into())];
    q.extend(query.iter().cloned());
    call(ctx, token, ctx.eng.app.http.request(method, url).query(&q).header("content-type", format!("multipart/related; boundary={b}")).body(body)).await
}

fn upload_query(o: &Value) -> Vec<(&'static str, String)> {
    let mut q = vec![];
    for k in ["keepRevisionForever", "ocrLanguage", "useContentAsIndexableText"] {
        if !o[k].is_null() && o[k] != "" {
            q.push((k, as_str(&o[k])));
        }
    }
    if o["simplifyOutput"] == false {
        q.push(("fields", "*".into()));
    }
    q
}

/// n8n keys/values collections (appPropertiesUi, propertiesUi) into Drive metadata.
fn properties(o: &Value, meta: &mut Value) {
    for (ui, values, key) in [("appPropertiesUi", "appPropertyValues", "appProperties"), ("propertiesUi", "propertyValues", "properties")] {
        let list = o[ui][values].as_array().cloned().unwrap_or_default();
        if !list.is_empty() {
            meta[key] = Value::Object(list.iter().map(|p| (as_str(&p["key"]), p["value"].clone())).collect());
        }
    }
}

pub async fn drive(ctx: &Ctx<'_>) -> Result<Out> {
    let (resource, op) = (ctx.s("resource", 0, "file")?, ctx.s("operation", 0, "upload")?);
    if resource == "drive" && op != "create" {
        return generated(ctx).await;
    }
    let token = ctx.oauth_token("googleDriveOAuth2Api").await?;
    let base = rebase("https://www.googleapis.com/drive/v3".into());
    let http = &ctx.eng.app.http;
    let all = ("supportsAllDrives", "true");
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let o = ctx.val(if resource == "fileFolder" { "filter" } else { "options" }, i, json!({}))?;
        match (resource.as_str(), op.as_str()) {
            ("file", "upload") => {
                let field = ctx.s("inputDataFieldName", i, "data")?;
                let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                let name = { let n = ctx.s("name", i, "")?; if n.is_empty() { bin["fileName"].as_str().unwrap_or("Untitled").to_string() } else { n } };
                let mime = bin["mimeType"].as_str().filter(|m| !m.is_empty()).unwrap_or("application/octet-stream").to_string();
                let mut meta = json!({"name": name, "parents": [parent(ctx, i)?]});
                properties(&o, &mut meta);
                out.push(Item::paired(upload(ctx, &token, reqwest::Method::POST, "", &meta, &mime, binary_bytes(bin)?, &upload_query(&o)).await?, i));
            }
            ("file", "createFromText") => {
                let mut meta = json!({"name": ctx.s("name", i, "")?, "parents": [parent(ctx, i)?]});
                if o["convertToGoogleDocument"] == true {
                    meta["mimeType"] = json!("application/vnd.google-apps.document");
                }
                properties(&o, &mut meta);
                let content = ctx.s("content", i, "")?.into_bytes();
                out.push(Item::paired(upload(ctx, &token, reqwest::Method::POST, "", &meta, "text/plain", content, &upload_query(&o)).await?, i));
            }
            ("file", "download") => {
                let id = drive_id(&ctx.s("fileId", i, "")?);
                let meta = call(ctx, &token, http.get(format!("{base}/files/{id}")).query(&[("fields", "id,name,mimeType"), all])).await?;
                let mime = as_str(&meta["mimeType"]);
                let conv = &o["googleFileConversion"]["conversion"];
                let pick = |k: &str, d: &str| conv[k].as_str().or(o[k].as_str()).filter(|s| !s.is_empty()).unwrap_or(d).to_string();
                let export = match mime.as_str() {
                    "application/vnd.google-apps.document" => Some(pick("docsToFormat", "text/html")),
                    "application/vnd.google-apps.spreadsheet" => Some(pick("sheetsToFormat", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
                    "application/vnd.google-apps.presentation" => Some(pick("slidesToFormat", "application/vnd.openxmlformats-officedocument.presentationml.presentation")),
                    "application/vnd.google-apps.drawing" => Some(pick("drawingsToFormat", "image/jpeg")),
                    _ => None,
                };
                let rb = match &export {
                    Some(m) => http.get(format!("{base}/files/{id}/export")).query(&[("mimeType", m.as_str())]),
                    None => http.get(format!("{base}/files/{id}")).query(&[("alt", "media"), all]),
                };
                let resp = rb.bearer_auth(&token).timeout(Duration::from_secs(300)).send().await?;
                if !resp.status().is_success() {
                    return Err(anyhow!("Google Drive download failed with {}", resp.status()));
                }
                let bytes = resp.bytes().await?;
                let out_mime = export.clone().unwrap_or(mime.clone());
                let mut name = { let n = opt(&o, "fileName"); if n.is_empty() { as_str(&meta["name"]) } else { n } };
                if export.is_some() && !name.contains('.') {
                    let ext = match out_mime.as_str() {
                        "text/html" => "html", "application/pdf" => "pdf", "text/plain" => "txt", "text/csv" => "csv", "image/jpeg" => "jpg", "image/png" => "png",
                        "image/svg+xml" => "svg", m if m.contains("wordprocessingml") => "docx", m if m.contains("spreadsheetml") => "xlsx",
                        m if m.contains("presentationml") => "pptx", _ => "",
                    };
                    if !ext.is_empty() {
                        name = format!("{name}.{ext}");
                    }
                }
                let mut item = ctx.items()[i].clone();
                item.paired_item = Some(json!({"item": i}));
                let mut bin = item.binary.take().unwrap_or_default();
                bin.insert(o["binaryPropertyName"].as_str().filter(|s| !s.is_empty()).unwrap_or("data").to_string(), make_binary(&bytes, &out_mime, &name));
                item.binary = Some(bin);
                out.push(item);
            }
            ("file", "copy") => {
                let id = drive_id(&ctx.s("fileId", i, "")?);
                let mut body = json!({});
                for (k, v) in [("name", ctx.s("name", i, "")?), ("description", opt(&o, "description"))] {
                    if !v.is_empty() {
                        body[k] = json!(v);
                    }
                }
                if o["copyRequiresWriterPermission"] == true {
                    body["copyRequiresWriterPermission"] = json!(true);
                }
                if !ctx.b("sameFolder", i, true)? {
                    body["parents"] = json!([parent(ctx, i)?]);
                }
                out.push(Item::paired(call(ctx, &token, http.post(format!("{base}/files/{id}/copy")).query(&[all]).json(&body)).await?, i));
            }
            ("file", "deleteFile") | ("folder", "deleteFolder") => {
                let id = drive_id(&ctx.s(if resource == "file" { "fileId" } else { "folderNoRootId" }, i, "")?);
                if o["deletePermanently"] == true {
                    call(ctx, &token, http.delete(format!("{base}/files/{id}")).query(&[all])).await?;
                } else {
                    call(ctx, &token, http.patch(format!("{base}/files/{id}")).query(&[all]).json(&json!({"trashed": true}))).await?;
                }
                let key = if resource == "file" { "fileId" } else { "folderId" };
                let mut done = json!({"success": true});
                done[key] = json!(id);
                out.push(Item::paired(done, i));
            }
            ("file", "move") => {
                let id = drive_id(&ctx.s("fileId", i, "")?);
                let cur = call(ctx, &token, http.get(format!("{base}/files/{id}")).query(&[("fields", "parents"), all])).await?;
                let old = cur["parents"].as_array().into_iter().flatten().map(as_str).collect::<Vec<_>>().join(",");
                let q = [("addParents", parent(ctx, i)?), ("removeParents", old), ("supportsAllDrives", "true".into())];
                out.push(Item::paired(call(ctx, &token, http.patch(format!("{base}/files/{id}")).query(&q).json(&json!({}))).await?, i));
            }
            ("file", "share") | ("folder", "share") => {
                let id = drive_id(&ctx.s(if resource == "file" { "fileId" } else { "folderNoRootId" }, i, "")?);
                // n8n keeps the permission in permissionsUi.permissionsValues; the editor shows flat fields.
                let ui = ctx.val("permissionsUi", i, json!({}))?;
                let pv = if ui["permissionsValues"].is_object() { ui["permissionsValues"].clone() } else {
                    json!({"role": ctx.s("role", i, "reader")?, "type": ctx.s("type", i, "user")?, "emailAddress": ctx.s("emailAddress", i, "")?,
                           "domain": ctx.s("domain", i, "")?, "allowFileDiscovery": ctx.b("allowFileDiscovery", i, false)?})
                };
                let mut perm = json!({"role": pv["role"], "type": pv["type"]});
                match as_str(&pv["type"]).as_str() {
                    "user" | "group" => perm["emailAddress"] = pv["emailAddress"].clone(),
                    "domain" => {
                        perm["domain"] = pv["domain"].clone();
                        perm["allowFileDiscovery"] = pv["allowFileDiscovery"].clone();
                    }
                    "anyone" => perm["allowFileDiscovery"] = pv["allowFileDiscovery"].clone(),
                    _ => {}
                }
                let mut q: Vec<(String, String)> = vec![("supportsAllDrives".into(), "true".into())];
                for k in ["emailMessage", "sendNotificationEmail", "transferOwnership", "moveToNewOwnersRoot", "useDomainAdminAccess"] {
                    if !o[k].is_null() && o[k] != "" {
                        q.push((k.into(), as_str(&o[k])));
                    }
                }
                out.push(Item::paired(call(ctx, &token, http.post(format!("{base}/files/{id}/permissions")).query(&q).json(&perm)).await?, i));
            }
            ("file", "update") => {
                let id = drive_id(&ctx.s("fileToUpdate", i, "")?);
                let mut meta = json!({});
                if !ctx.s("newUpdatedFileName", i, "")?.is_empty() {
                    meta["name"] = json!(ctx.s("newUpdatedFileName", i, "")?);
                }
                for k in ["trashed", "starred", "description"] {
                    if !o[k].is_null() && o[k] != "" {
                        meta[k] = o[k].clone();
                    }
                }
                let v = if ctx.b("changeFileContent", i, false)? {
                    let field = ctx.s("inputDataFieldName", i, "data")?;
                    let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                    let mime = bin["mimeType"].as_str().unwrap_or("application/octet-stream").to_string();
                    upload(ctx, &token, reqwest::Method::PATCH, &format!("/{id}"), &meta, &mime, binary_bytes(bin)?, &upload_query(&o)).await?
                } else {
                    call(ctx, &token, http.patch(format!("{base}/files/{id}")).query(&[all]).json(&meta)).await?
                };
                out.push(Item::paired(v, i));
            }
            ("fileFolder", "search") => {
                let mut q = vec![];
                let text = ctx.s("queryString", i, "")?;
                if ctx.s("searchMethod", i, "name")? == "query" {
                    if !text.is_empty() {
                        q.push(format!("({text})"));
                    }
                } else if !text.is_empty() {
                    q.push(format!("name contains '{}'", text.replace('\\', "\\\\").replace('\'', "\\'")));
                }
                let folder = drive_id(&opt(&o, "folderId"));
                if !folder.is_empty() {
                    q.push(format!("'{folder}' in parents"));
                }
                match o["whatToSearch"].as_str() {
                    Some("files") => q.push(format!("mimeType != '{FOLDER}'")),
                    Some("folders") => q.push(format!("mimeType = '{FOLDER}'")),
                    _ => {}
                }
                let types: Vec<String> = match &o["fileTypes"] { Value::Array(a) => a.iter().map(as_str).collect(), v => as_str(v).split(',').map(|s| s.trim().to_string()).collect() };
                let types: Vec<String> = types.into_iter().filter(|t| !t.is_empty() && t != "*").collect();
                if !types.is_empty() {
                    q.push(format!("({})", types.iter().map(|t| format!("mimeType = '{t}'")).collect::<Vec<_>>().join(" or ")));
                }
                if o["includeTrashed"] != true {
                    q.push("trashed = false".into());
                }
                let mut query: Vec<(String, String)> = vec![("q".into(), q.join(" and ")), ("fields".into(), "nextPageToken, files(id, name, mimeType, parents, webViewLink, modifiedTime)".into()),
                                                         ("supportsAllDrives".into(), "true".into()), ("includeItemsFromAllDrives".into(), "true".into())];
                let drv = drive_id(&opt(&o, "driveId"));
                if !drv.is_empty() && drv != "My Drive" {
                    query.extend([("corpora".to_string(), "drive".to_string()), ("driveId".to_string(), drv)]);
                }
                let limit = limit_of(ctx, i)?;
                let mut page: Option<String> = None;
                let mut found = vec![];
                loop {
                    let mut qq = query.clone();
                    qq.push(("pageSize".into(), limit.map(|l| (l - found.len()).min(1000)).unwrap_or(1000).to_string()));
                    if let Some(p) = &page {
                        qq.push(("pageToken".into(), p.clone()));
                    }
                    let v = call(ctx, &token, http.get(format!("{base}/files")).query(&qq)).await?;
                    found.extend(v["files"].as_array().cloned().unwrap_or_default());
                    page = v["nextPageToken"].as_str().map(String::from);
                    if page.is_none() || limit.is_some_and(|l| found.len() >= l) {
                        break;
                    }
                }
                if let Some(l) = limit {
                    found.truncate(l);
                }
                out.extend(found.into_iter().map(|f| Item::paired(f, i)));
            }
            ("folder", "create") => {
                let name = ctx.s("name", i, "")?;
                let name = if name.is_empty() { "Untitled".to_string() } else { name };
                let mut meta = json!({"name": name, "mimeType": FOLDER, "parents": [parent(ctx, i)?]});
                if !opt(&o, "folderColorRgb").is_empty() {
                    meta["folderColorRgb"] = json!(opt(&o, "folderColorRgb"));
                }
                let mut q = vec![all];
                if o["simplifyOutput"] == false {
                    q.push(("fields", "*"));
                }
                out.push(Item::paired(call(ctx, &token, http.post(format!("{base}/files")).query(&q).json(&meta)).await?, i));
            }
            ("drive", "create") => {
                let mut body = json!({"name": ctx.s("name", i, "")?});
                for k in ["colorRgb", "hidden"] {
                    if !o[k].is_null() && o[k] != "" {
                        body[k] = o[k].clone();
                    }
                }
                let rid = uuid::Uuid::new_v4().to_string();
                out.push(Item::paired(call(ctx, &token, http.post(format!("{base}/drives")).query(&[("requestId", rid.as_str())]).json(&body)).await?, i));
            }
            (r, o) => return Err(anyhow!("Google Drive operation \"{r}:{o}\" is not supported")),
        }
    }
    Ok(vec![out])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_from_urls() {
        assert_eq!(drive_id("https://docs.google.com/document/d/1AbC_x/edit"), "1AbC_x");
        assert_eq!(drive_id("https://drive.google.com/drive/folders/0Bxyz?usp=sharing"), "0Bxyz");
        assert_eq!(drive_id("plainId"), "plainId");
    }

    #[test]
    fn gmail_search_from_filters() {
        let q = gmail_query(&json!({"q": "invoice", "readStatus": "unread", "sender": "a@x.io", "receivedAfter": "2026-01-01T00:00:00Z"}));
        assert_eq!(q, "invoice is:unread from:a@x.io after:1767225600");
    }

    #[test]
    fn mime_with_attachment() {
        let raw = mime(&[("To", "a@x.io".into()), ("Subject", encode_header("Grüße")), ("Cc", String::new())], false, "Hallo", &[("a.txt".into(), "text/plain".into(), b"hi".to_vec())]);
        let text = String::from_utf8(URL_SAFE_NO_PAD.decode(raw).unwrap()).unwrap();
        assert!(text.contains("To: a@x.io\r\n") && !text.contains("Cc:") && text.contains("=?UTF-8?B?R3LDvMOfZQ==?="));
        assert!(text.contains("multipart/mixed") && text.contains("filename=\"a.txt\"") && text.contains(&STANDARD.encode("hi")));
    }
}
