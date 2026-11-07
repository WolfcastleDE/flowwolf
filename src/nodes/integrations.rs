//! Nodes that talk to external services.

use super::data::{binary_bytes, make_binary, mime_for};
use super::{Ctx, Out, rl_value};
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Map, Value, json};
use std::time::Duration;

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    Some(match kind {
        "n8n-nodes-base.httpRequest" => http_request(ctx).await,
        "n8n-nodes-base.emailSend" => email(ctx).await,
        "n8n-nodes-base.postgres" => postgres(ctx).await,
        "n8n-nodes-base.redis" => redis_node(ctx).await,
        "n8n-nodes-base.slack" => slack(ctx).await,
        "n8n-nodes-base.telegram" => telegram(ctx).await,
        "n8n-nodes-base.discord" => discord(ctx).await,
        "n8n-nodes-base.gmail" => super::google::gmail(ctx).await,
        "n8n-nodes-base.awsS3" => super::s3::run(ctx).await,
        "n8n-nodes-base.mongoDb" => super::mongo::run(ctx).await,
        "n8n-nodes-base.ftp" => super::ftp::run(ctx).await,
        "n8n-nodes-base.googleDrive" => super::google::drive(ctx).await,
        "@n8n/n8n-nodes-langchain.openAi" => openai(ctx).await,
        "@n8n/n8n-nodes-langchain.anthropic" => anthropic(ctx).await,
        "n8n-nodes-base.executeCommand" => execute_command(ctx).await,
        "n8n-nodes-base.mqtt" => mqtt(ctx).await,
        _ => return None,
    })
}

fn pairs(list: Vec<Value>) -> Vec<(String, String)> {
    list.iter().filter(|p| !as_str(&p["name"]).is_empty()).map(|p| (as_str(&p["name"]), as_str(&p["value"]))).collect()
}

fn json_param(v: Value, what: &str) -> Result<Value> {
    match v {
        Value::String(s) if s.trim().is_empty() => Ok(json!({})),
        Value::String(s) => serde_json::from_str(&s).map_err(|e| anyhow!("{what} must be valid JSON: {e}")),
        v => Ok(v),
    }
}

/// Applies credential based authentication to a request.
fn apply_auth(mut rb: reqwest::RequestBuilder, kind: &str, c: &Value) -> reqwest::RequestBuilder {
    let s = |k: &str| as_str(&c[k]);
    match kind {
        "httpBasicAuth" => rb.basic_auth(s("user"), Some(s("password"))),
        "httpHeaderAuth" => rb.header(s("name"), s("value")),
        "httpQueryAuth" => rb.query(&[(s("name"), s("value"))]),
        "httpBearerAuth" => rb.bearer_auth(s("token")),
        "httpCustomAuth" => {
            let j: Value = serde_json::from_str(&s("json")).unwrap_or(json!({}));
            for (k, v) in j["headers"].as_object().into_iter().flatten() {
                rb = rb.header(k, as_str(v));
            }
            if let Some(q) = j["qs"].as_object() {
                rb = rb.query(&q.iter().map(|(k, v)| (k.clone(), as_str(v))).collect::<Vec<_>>());
            }
            rb
        }
        "anthropicApi" => rb.header("x-api-key", s("apiKey")).header("anthropic-version", "2023-06-01"),
        // Most predefined API credentials are a bearer token under one of these names.
        _ => {
            let token = ["accessToken", "apiKey", "apiToken", "token", "secretKey"].iter().map(|k| s(k)).find(|t| !t.is_empty()).unwrap_or_default();
            rb.bearer_auth(token)
        }
    }
}

async fn http_request(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    let batch_size = ctx.n("options.batching.batch.batchSize", 0, 50.0)?.max(1.0) as usize;
    let batch_interval = ctx.n("options.batching.batch.batchInterval", 0, 0.0)? as u64;
    for (i, _) in ctx.items().iter().enumerate() {
        if i > 0 && batch_interval > 0 && i % batch_size == 0 {
            tokio::time::sleep(Duration::from_millis(batch_interval)).await;
        }
        match http_one(ctx, i).await {
            Ok(items) => out.extend(items),
            Err(e) if ctx.node.on_error_mode() == "continueRegularOutput" => out.push(Item::paired(json!({"error": {"message": e.to_string()}}), i)),
            Err(e) => return Err(e),
        }
    }
    Ok(vec![out])
}

async fn http_one(ctx: &Ctx<'_>, i: usize) -> Result<Vec<Item>> {
    let app = &ctx.eng.app;
    let method = ctx.s("method", i, "GET")?.to_uppercase();
    let url = ctx.s("url", i, "")?;
    if url.is_empty() {
        return Err(anyhow!("The URL parameter is empty"));
    }
    let insecure = ctx.b("options.allowUnauthorizedCerts", i, false)?;
    let client = if insecure { &app.http_insecure } else { &app.http };
    let mut rb = client.request(reqwest::Method::from_bytes(method.as_bytes())?, &url);

    match ctx.s("authentication", i, "none")?.as_str() {
        mode @ ("genericCredentialType" | "predefinedCredentialType") => {
            let kind = ctx.s(if mode == "genericCredentialType" { "genericAuthType" } else { "nodeCredentialType" }, i, "")?;
            rb = if crate::oauth::is_oauth(&kind) { rb.bearer_auth(ctx.oauth_token(&kind).await?) } else { apply_auth(rb, &kind, &ctx.creds(&kind)?) };
        }
        _ => {}
    }

    if ctx.b("sendQuery", i, false)? {
        if ctx.s("specifyQuery", i, "keypair")? == "json" {
            let q = json_param(ctx.val("jsonQuery", i, json!({}))?, "Query parameters")?;
            rb = rb.query(&q.as_object().into_iter().flatten().map(|(k, v)| (k.clone(), as_str(v))).collect::<Vec<_>>());
        } else {
            rb = rb.query(&pairs(ctx.list("queryParameters.parameters", i)?));
        }
    }
    if ctx.b("sendHeaders", i, false)? {
        let hs = if ctx.s("specifyHeaders", i, "keypair")? == "json" {
            json_param(ctx.val("jsonHeaders", i, json!({}))?, "Headers")?.as_object().into_iter().flatten().map(|(k, v)| (k.clone(), as_str(v))).collect()
        } else {
            pairs(ctx.list("headerParameters.parameters", i)?)
        };
        for (k, v) in hs {
            rb = rb.header(k, v);
        }
    }
    if ctx.b("sendBody", i, false)? {
        let ct = ctx.s("contentType", i, "json")?;
        let keypair = ctx.s("specifyBody", i, "keypair")? != "json";
        rb = match ct.as_str() {
            "json" => {
                if keypair {
                    let o: Map<String, Value> = ctx
                        .list("bodyParameters.parameters", i)?
                        .into_iter()
                        .filter(|p| !as_str(&p["name"]).is_empty())
                        .map(|p| (as_str(&p["name"]), p["value"].clone()))
                        .collect();
                    rb.json(&Value::Object(o))
                } else {
                    rb.json(&json_param(ctx.val("jsonBody", i, json!({}))?, "JSON body")?)
                }
            }
            "form-urlencoded" => {
                if keypair {
                    rb.form(&pairs(ctx.list("bodyParameters.parameters", i)?))
                } else {
                    rb.header("content-type", "application/x-www-form-urlencoded").body(ctx.s("body", i, "")?)
                }
            }
            "multipart-form-data" => {
                let mut form = reqwest::multipart::Form::new();
                for p in ctx.list("bodyParameters.parameters", i)? {
                    let name = as_str(&p["name"]);
                    if p["parameterType"] == "formBinaryData" {
                        let field = as_str(&p["inputDataFieldName"]);
                        let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                        let part = reqwest::multipart::Part::bytes(binary_bytes(bin)?)
                            .file_name(as_str(&bin["fileName"]))
                            .mime_str(bin["mimeType"].as_str().unwrap_or("application/octet-stream"))?;
                        form = form.part(name, part);
                    } else {
                        form = form.text(name, as_str(&p["value"]));
                    }
                }
                rb.multipart(form)
            }
            "binaryData" => {
                let field = ctx.s("inputDataFieldName", i, "data")?;
                let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                rb.header("content-type", as_str(&bin["mimeType"])).body(binary_bytes(bin)?)
            }
            _ => rb.header("content-type", ctx.s("rawContentType", i, "text/plain")?).body(ctx.s("body", i, "")?),
        };
    }
    let timeout = ctx.n("options.timeout", i, 300_000.0)?;
    rb = rb.timeout(Duration::from_millis(timeout as u64));

    let resp = rb.send().await.map_err(|e| anyhow!("Request failed: {e}"))?;
    let status = resp.status();
    let headers: Map<String, Value> = resp.headers().iter().map(|(k, v)| (k.to_string(), json!(v.to_str().unwrap_or("")))).collect();
    let ctype = resp.headers().get("content-type").and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let bytes = resp.bytes().await?;

    let never_error = ctx.b("options.response.response.neverError", i, false)?;
    if !status.is_success() && !never_error {
        let body = String::from_utf8_lossy(&bytes);
        return Err(anyhow!("Request failed with status code {}: {}", status.as_u16(), body.chars().take(500).collect::<String>()));
    }
    let full = ctx.b("options.response.response.fullResponse", i, false)?;
    let format = ctx.s("options.response.response.responseFormat", i, "autodetect")?;
    let out_prop = ctx.s("options.response.response.outputPropertyName", i, "data")?;
    let is_json = format == "json" || (format == "autodetect" && ctype.contains("json"));
    let is_text = format == "text" || (format == "autodetect" && (ctype.starts_with("text/") || ctype.contains("xml") || ctype.is_empty()));

    if format == "file" || (!is_json && !is_text) {
        let name = url.rsplit('/').next().filter(|s| !s.is_empty() && s.len() < 100).unwrap_or("file").split('?').next().unwrap_or("file").to_string();
        let mime = if ctype.is_empty() { mime_for(&name).to_string() } else { ctype.split(';').next().unwrap_or("").to_string() };
        let mut m = Map::new();
        m.insert(out_prop, make_binary(&bytes, &mime, &name));
        let j = if full { json!({"headers": headers, "statusCode": status.as_u16(), "statusMessage": status.canonical_reason()}) } else { json!({}) };
        return Ok(vec![Item { json: j, binary: Some(m), paired_item: None }]);
    }
    let body: Value = if is_json {
        serde_json::from_slice(&bytes).unwrap_or_else(|_| json!(String::from_utf8_lossy(&bytes)))
    } else {
        json!(String::from_utf8_lossy(&bytes))
    };
    if full {
        return Ok(vec![Item::new(json!({"body": body, "headers": headers, "statusCode": status.as_u16(), "statusMessage": status.canonical_reason()}))]);
    }
    Ok(match body {
        Value::Array(a) => a.into_iter().map(|v| if v.is_object() { Item::new(v) } else { Item::new(json!({ out_prop.clone(): v })) }).collect(),
        Value::Object(_) => vec![Item::new(body)],
        v => vec![Item::new(json!({ out_prop: v }))],
    })
}

async fn email(ctx: &Ctx<'_>) -> Result<Out> {
    use lettre::message::{Mailbox, MultiPart, header::ContentType};
    use lettre::transport::smtp::authentication::Credentials;
    use lettre::transport::smtp::client::{Tls, TlsParameters};
    use lettre::{AsyncSmtpTransport, AsyncTransport, Message, Tokio1Executor};

    let c = ctx.creds("smtp")?;
    let host = as_str(&c["host"]);
    let port = as_f64(&c["port"]).unwrap_or(465.0) as u16;
    let secure = c["secure"].as_bool().unwrap_or(true);
    let insecure = ctx.b("options.allowUnauthorizedCerts", 0, false)?;
    let tls_params = TlsParameters::builder(host.clone()).dangerous_accept_invalid_certs(insecure).build()?;
    let mut builder = AsyncSmtpTransport::<Tokio1Executor>::builder_dangerous(&host).port(port).tls(if secure {
        Tls::Wrapper(tls_params)
    } else if c["disableStartTls"].as_bool() == Some(true) {
        Tls::None
    } else {
        Tls::Opportunistic(tls_params)
    });
    if !as_str(&c["user"]).is_empty() {
        builder = builder.credentials(Credentials::new(as_str(&c["user"]), as_str(&c["password"])));
    }
    let transport = builder.timeout(Some(Duration::from_secs(30))).build();
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let parse = |s: &str| -> Result<Vec<Mailbox>> {
            s.split(',').map(|x| x.trim()).filter(|x| !x.is_empty()).map(|x| x.parse::<Mailbox>().map_err(|e| anyhow!("Invalid email address '{x}': {e}"))).collect()
        };
        let mut m = Message::builder().from(ctx.s("fromEmail", i, "")?.parse().map_err(|e| anyhow!("Invalid 'From Email': {e}"))?).subject(ctx.s("subject", i, "")?);
        for to in parse(&ctx.s("toEmail", i, "")?)? {
            m = m.to(to);
        }
        for cc in parse(&ctx.s("options.ccEmail", i, "")?)? {
            m = m.cc(cc);
        }
        for bcc in parse(&ctx.s("options.bccEmail", i, "")?)? {
            m = m.bcc(bcc);
        }
        if let Some(r) = parse(&ctx.s("options.replyTo", i, "")?)?.into_iter().next() {
            m = m.reply_to(r);
        }
        let format = ctx.s("emailFormat", i, "text")?;
        let (text, html) = (ctx.s("text", i, "")?, ctx.s("html", i, "")?);
        let msg = match format.as_str() {
            "html" => m.header(ContentType::TEXT_HTML).body(html)?,
            "both" => m.multipart(MultiPart::alternative_plain_html(text, html))?,
            _ => m.header(ContentType::TEXT_PLAIN).body(text)?,
        };
        let r = transport.send(msg).await.map_err(|e| anyhow!("Sending email failed: {e}"))?;
        out.push(Item::paired(json!({"accepted": true, "response": r.message().collect::<Vec<_>>().join(" "), "code": r.code().to_string()}), i));
    }
    Ok(vec![out])
}

/// Renders a JSON value as a safe PostgreSQL literal.
pub fn pg_lit(v: &Value) -> String {
    match v {
        Value::Null => "NULL".into(),
        Value::Bool(b) => if *b { "TRUE" } else { "FALSE" }.into(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => format!("'{}'", s.replace('\'', "''")),
        other => format!("'{}'", other.to_string().replace('\'', "''")),
    }
}

fn pg_ident(s: &str) -> String {
    format!("\"{}\"", s.replace('"', "\"\""))
}

async fn pg_query(client: &tokio_postgres::Client, sql: &str) -> Result<Vec<Item>> {
    let trimmed = sql.trim().trim_end_matches(';');
    let lower = trimmed.to_lowercase();
    let returns_rows = lower.starts_with("select") || lower.starts_with("with") || lower.starts_with("values") || lower.contains(" returning ");
    if !returns_rows {
        client.batch_execute(trimmed).await?;
        return Ok(vec![Item::new(json!({"success": true}))]);
    }
    // Let PostgreSQL build the JSON so every column type converts correctly.
    let wrapped = if lower.starts_with("select") || lower.starts_with("values") {
        format!("SELECT COALESCE(json_agg(t), '[]')::text FROM ({trimmed}) t")
    } else {
        format!("WITH t AS ({trimmed}) SELECT COALESCE(json_agg(t), '[]')::text FROM t")
    };
    let rows = client.simple_query(&wrapped).await?;
    for r in rows {
        if let tokio_postgres::SimpleQueryMessage::Row(row) = r {
            let v: Value = serde_json::from_str(row.get(0).unwrap_or("[]"))?;
            return Ok(to_items(v));
        }
    }
    Ok(vec![])
}

/// Connects with TLS unless the credential says `ssl: disable`.
pub(crate) async fn pg_connect(c: &Value) -> Result<tokio_postgres::Client> {
    let mut cfg = tokio_postgres::Config::new();
    cfg.host(&as_str(&c["host"]))
        .port(as_f64(&c["port"]).unwrap_or(5432.0) as u16)
        .dbname(&as_str(&c["database"]))
        .user(&as_str(&c["user"]))
        .password(as_str(&c["password"]))
        .connect_timeout(Duration::from_secs(20));
    // Include the cause (e.g. "invalid peer certificate: UnknownIssuer"), tokio-postgres hides it.
    let fail = |e: tokio_postgres::Error| {
        let cause = std::error::Error::source(&e).map(|s| format!(" ({s})")).unwrap_or_default();
        anyhow!("Could not connect to Postgres: {e}{cause}")
    };
    if c["ssl"].as_str().unwrap_or("disable") == "disable" {
        let (client, conn) = cfg.connect(tokio_postgres::NoTls).await.map_err(fail)?;
        tokio::spawn(conn);
        return Ok(client);
    }
    use std::sync::Arc;
    let builder = rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider())).with_safe_default_protocol_versions()?;
    let tls = if c["allowUnauthorizedCerts"] == true {
        builder.dangerous().with_custom_certificate_verifier(Arc::new(AcceptAnyCert)).with_no_client_auth()
    } else {
        builder.with_root_certificates(rustls::RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() }).with_no_client_auth()
    };
    cfg.ssl_mode(tokio_postgres::config::SslMode::Require);
    let (client, conn) = cfg.connect(tokio_postgres_rustls::MakeRustlsConnect::new(tls)).await.map_err(fail)?;
    tokio::spawn(conn);
    Ok(client)
}

/// Used only when the credential explicitly allows self-signed certificates.
#[derive(Debug)]
pub struct AcceptAnyCert;

impl rustls::client::danger::ServerCertVerifier for AcceptAnyCert {
    fn verify_server_cert(
        &self,
        _: &rustls::pki_types::CertificateDer<'_>,
        _: &[rustls::pki_types::CertificateDer<'_>],
        _: &rustls::pki_types::ServerName<'_>,
        _: &[u8],
        _: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(&self, _: &[u8], _: &rustls::pki_types::CertificateDer<'_>, _: &rustls::DigitallySignedStruct) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }
    fn verify_tls13_signature(&self, _: &[u8], _: &rustls::pki_types::CertificateDer<'_>, _: &rustls::DigitallySignedStruct) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }
    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        rustls::crypto::ring::default_provider().signature_verification_algorithms.supported_schemes()
    }
}

async fn postgres(ctx: &Ctx<'_>) -> Result<Out> {
    let client = pg_connect(&ctx.creds("postgres")?).await?;
    let op = ctx.s("operation", 0, "executeQuery")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let sql = match op.as_str() {
            "executeQuery" => {
                let mut q = ctx.s("query", i, "")?;
                let repl = ctx.val("options.queryReplacement", i, json!(""))?;
                let vals: Vec<Value> = match repl {
                    Value::Array(a) => a,
                    Value::String(s) if !s.is_empty() => s.split(',').map(|x| json!(x.trim())).collect(),
                    Value::String(_) => vec![],
                    v => vec![v],
                };
                for (n, v) in vals.iter().enumerate().rev() {
                    q = q.replace(&format!("${}", n + 1), &pg_lit(v));
                }
                q
            }
            "insert" | "upsert" => {
                let schema = ctx.s("schema", i, "public")?;
                let table = ctx.s("table", i, "")?;
                let obj = if ctx.s("columns.mappingMode", i, "autoMapInputData")? == "defineBelow" {
                    ctx.val("columns.value", i, json!({}))?
                } else {
                    it.json.clone()
                };
                let o = obj.as_object().cloned().unwrap_or_default();
                let cols: Vec<String> = o.keys().map(|k| pg_ident(k)).collect();
                let vals: Vec<String> = o.values().map(pg_lit).collect();
                let mut q = format!("INSERT INTO {}.{} ({}) VALUES ({})", pg_ident(&schema), pg_ident(&table), cols.join(", "), vals.join(", "));
                if op == "upsert" {
                    let key = ctx.s("columns.matchingColumns", i, "id")?;
                    let sets: Vec<String> = o.keys().map(|k| format!("{0} = EXCLUDED.{0}", pg_ident(k))).collect();
                    q.push_str(&format!(" ON CONFLICT ({}) DO UPDATE SET {}", pg_ident(&key), sets.join(", ")));
                }
                q + " RETURNING *"
            }
            "select" => {
                let schema = ctx.s("schema", i, "public")?;
                let table = ctx.s("table", i, "")?;
                let limit = if ctx.b("returnAll", i, false)? { String::new() } else { format!(" LIMIT {}", ctx.n("limit", i, 50.0)? as i64) };
                format!("SELECT * FROM {}.{}{}", pg_ident(&schema), pg_ident(&table), limit)
            }
            "deleteTable" => {
                let table = ctx.s("table", i, "")?;
                format!("TRUNCATE {}.{}", pg_ident(&ctx.s("schema", i, "public")?), pg_ident(&table))
            }
            o => return Err(anyhow!("Operation \"{o}\" is not supported")),
        };
        for mut item in pg_query(&client, &sql).await? {
            item.paired_item = Some(json!({"item": i}));
            out.push(item);
        }
        if op == "executeQuery" && ctx.node.execute_once {
            break;
        }
    }
    Ok(vec![out])
}

async fn redis_node(ctx: &Ctx<'_>) -> Result<Out> {
    let c = ctx.creds("redis")?;
    let pw = as_str(&c["password"]);
    let user = as_str(&c["user"]);
    let auth = if pw.is_empty() { String::new() } else { format!("{}:{}@", urlencode(&user), urlencode(&pw)) };
    let url = format!("redis://{auth}{}:{}/{}", as_str(&c["host"]), as_f64(&c["port"]).unwrap_or(6379.0), as_f64(&c["database"]).unwrap_or(0.0));
    let client = redis::Client::open(url)?;
    let mut con = client.get_multiplexed_async_connection().await.map_err(|e| anyhow!("Could not connect to Redis: {e}"))?;
    let op = ctx.s("operation", 0, "info")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let key = ctx.s("key", i, "")?;
        let mut j = it.json.clone();
        match op.as_str() {
            "get" => {
                let v: Option<String> = redis::cmd("GET").arg(&key).query_async(&mut con).await?;
                let v = v.map(|s| serde_json::from_str(&s).unwrap_or(json!(s))).unwrap_or(Value::Null);
                set_path(&mut j, &ctx.s("propertyName", i, "propertyName")?, v);
            }
            "set" => {
                let v = ctx.val("value", i, json!(""))?;
                let mut cmd = redis::cmd("SET");
                cmd.arg(&key).arg(as_str(&v));
                if ctx.b("expire", i, false)? {
                    cmd.arg("EX").arg(ctx.n("ttl", i, 60.0)? as u64);
                }
                let _: () = cmd.query_async(&mut con).await?;
            }
            "delete" => {
                let _: i64 = redis::cmd("DEL").arg(&key).query_async(&mut con).await?;
            }
            "incr" => {
                let n: i64 = redis::cmd("INCR").arg(&key).query_async(&mut con).await?;
                if ctx.b("expire", i, false)? {
                    let _: i64 = redis::cmd("EXPIRE").arg(&key).arg(ctx.n("ttl", i, 60.0)? as u64).query_async(&mut con).await?;
                }
                j[key.as_str()] = json!(n);
            }
            "keys" => {
                let keys: Vec<String> = redis::cmd("KEYS").arg(ctx.s("keyPattern", i, "*")?).query_async(&mut con).await?;
                let mut o = Map::new();
                for k in keys {
                    let v: Option<String> = if ctx.b("getValues", i, true)? { redis::cmd("GET").arg(&k).query_async(&mut con).await.unwrap_or(None) } else { None };
                    o.insert(k, v.map(Value::String).unwrap_or(Value::Null));
                }
                j = Value::Object(o);
            }
            "publish" => {
                let n: i64 = redis::cmd("PUBLISH").arg(ctx.s("channel", i, "")?).arg(ctx.s("messageData", i, "")?).query_async(&mut con).await?;
                j = json!({"published": true, "receivers": n});
            }
            "push" => {
                let cmd = if ctx.b("tail", i, false)? { "RPUSH" } else { "LPUSH" };
                let _: i64 = redis::cmd(cmd).arg(ctx.s("list", i, "")?).arg(ctx.s("messageData", i, "")?).query_async(&mut con).await?;
            }
            "pop" => {
                let cmd = if ctx.b("tail", i, false)? { "RPOP" } else { "LPOP" };
                let v: Option<String> = redis::cmd(cmd).arg(ctx.s("list", i, "")?).query_async(&mut con).await?;
                let v = v.map(|s| serde_json::from_str(&s).unwrap_or(json!(s))).unwrap_or(Value::Null);
                set_path(&mut j, &ctx.s("propertyName", i, "propertyName")?, v);
            }
            "info" => {
                let s: String = redis::cmd("INFO").query_async(&mut con).await?;
                let o: Map<String, Value> = s.lines().filter_map(|l| l.split_once(':')).map(|(k, v)| (k.to_string(), json!(v.trim()))).collect();
                j = Value::Object(o);
            }
            o => return Err(anyhow!("Operation \"{o}\" is not supported")),
        }
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

async fn post_json(ctx: &Ctx<'_>, url: &str, kind: &str, creds: &Value, body: &Value) -> Result<Value> {
    let rb = apply_auth(ctx.eng.app.http.post(url), kind, creds).json(body).timeout(Duration::from_secs(300));
    let resp = rb.send().await?;
    let status = resp.status();
    let text = resp.text().await?;
    let v: Value = serde_json::from_str(&text).unwrap_or(json!({"data": text}));
    if !status.is_success() {
        let msg = v["error"]["message"].as_str().or(v["description"].as_str()).or(v["message"].as_str()).map(String::from).unwrap_or(text);
        return Err(anyhow!("{} returned {}: {msg}", ctx.node.kind.rsplit('.').next().unwrap_or("API"), status.as_u16()));
    }
    Ok(v)
}

/// Runs the generated (declarative) definition of a node for the operations without own code.
pub(super) async fn generated(ctx: &Ctx<'_>) -> Result<Out> {
    super::declarative::run(ctx, &ctx.node.kind).await.unwrap_or_else(|| Err(anyhow!("{} has no generated operations", ctx.node.kind)))
}

/// Seconds since the epoch from a dateTime parameter (RFC 3339, "YYYY-MM-DDTHH:MM:SS" as UTC, or a number).
fn unix_seconds(v: &Value) -> Result<i64> {
    if let Some(n) = as_f64(v) {
        return Ok(n as i64);
    }
    let s = as_str(v);
    chrono::DateTime::parse_from_rfc3339(&s)
        .map(|d| d.timestamp())
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(&s, "%Y-%m-%dT%H:%M:%S").map(|d| d.and_utc().timestamp()))
        .map_err(|_| anyhow!("'{s}' is not a date"))
}

fn slack_ok(v: Value) -> Result<Value> {
    if v["ok"] == false {
        return Err(anyhow!("Slack error: {}", as_str(&v["error"])));
    }
    Ok(v)
}

/// Slack: sending, scheduling and deleting messages (channel or user) and file uploads are
/// handled here; all other operations come from the generated definition.
async fn slack(ctx: &Ctx<'_>) -> Result<Out> {
    let (resource, op) = (ctx.s("resource", 0, "message")?, ctx.s("operation", 0, "post")?);
    match (resource.as_str(), op.as_str()) {
        ("message", "post" | "schedule" | "delete") | ("file", "upload") => {}
        _ => return generated(ctx).await,
    }
    let c = ctx.creds("slackApi")?;
    let api = |m: &str| super::declarative::rebase(format!("https://slack.com/api/{m}"));
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        if resource == "file" {
            out.push(Item::paired(slack_upload(ctx, &c, i).await?, i));
            continue;
        }
        let target = if ctx.s("select", i, "channel")? == "user" { ctx.s("user", i, "")? } else { ctx.s("channelId", i, &ctx.s("channel", i, "")?)? };
        let mut body = json!({"channel": target});
        if op == "delete" {
            body["ts"] = json!(ctx.s("timestamp", i, "")?);
            out.push(Item::paired(slack_ok(post_json(ctx, &api("chat.delete"), "slackApi", &c, &body).await?)?, i));
            continue;
        }
        body["text"] = json!(ctx.s("text", i, "")?);
        match ctx.s("messageType", i, "text")?.as_str() {
            "block" => {
                let b = json_param(ctx.val("blocksUi", i, json!([]))?, "Blocks")?;
                body["blocks"] = if b.is_array() { b } else { b["blocks"].clone() };
            }
            "attachment" => body["attachments"] = json_param(ctx.val("attachments", i, json!([]))?, "Attachments")?,
            _ => {}
        }
        for (k, v) in ctx.val("otherOptions", i, json!({}))?.as_object().into_iter().flatten() {
            match k.as_str() {
                "includeLinkToWorkflow" | "appendAttribution" => {}
                // n8n keeps thread replies in a small collection
                "thread_ts" if v.is_object() => {
                    let r = &v["replyValues"];
                    body["thread_ts"] = r["thread_ts"].clone();
                    if !r["reply_broadcast"].is_null() {
                        body["reply_broadcast"] = r["reply_broadcast"].clone();
                    }
                }
                _ => body[k.as_str()] = v.clone(),
            }
        }
        let method = if op == "schedule" {
            body["post_at"] = json!(unix_seconds(&ctx.val("postAt", i, Value::Null)?)?);
            "chat.scheduleMessage"
        } else {
            "chat.postMessage"
        };
        out.push(Item::paired(slack_ok(post_json(ctx, &api(method), "slackApi", &c, &body).await?)?, i));
    }
    Ok(vec![out])
}

/// Slack's external upload: ask for an upload URL, send the bytes, then complete (and share) the file.
async fn slack_upload(ctx: &Ctx<'_>, c: &Value, i: usize) -> Result<Value> {
    let opts = ctx.val("options", i, json!({}))?;
    let (bytes, mut name) = if ctx.b("binaryData", i, true)? {
        let field = ctx.s("binaryPropertyName", i, "data")?;
        let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
        (binary_bytes(bin)?, as_str(&bin["fileName"]))
    } else {
        (ctx.s("fileContent", i, "")?.into_bytes(), String::new())
    };
    if let Some(n) = opts["fileName"].as_str().filter(|n| !n.is_empty()) {
        name = n.to_string();
    }
    if name.is_empty() {
        name = "file".into();
    }
    let api = |m: &str| super::declarative::rebase(format!("https://slack.com/api/{m}"));
    let token = as_str(&c["accessToken"]);
    let ticket: Value = ctx.eng.app.http.get(api("files.getUploadURLExternal")).bearer_auth(&token)
        .query(&[("filename", name.as_str()), ("length", &bytes.len().to_string())]).send().await?.json().await?;
    let ticket = slack_ok(ticket)?;
    let sent = ctx.eng.app.http.post(as_str(&ticket["upload_url"])).body(bytes).send().await?;
    if !sent.status().is_success() {
        return Err(anyhow!("Slack upload failed with {}", sent.status()));
    }
    let mut done = json!({"files": [{"id": ticket["file_id"], "title": opts["title"].as_str().unwrap_or(&name)}]});
    let channels = match &opts["channelIds"] {
        Value::Array(a) => a.iter().map(as_str).collect::<Vec<_>>().join(","),
        v => as_str(v),
    };
    if !channels.is_empty() {
        done["channels"] = json!(channels);
    }
    for (k, api_key) in [("initialComment", "initial_comment"), ("threadTs", "thread_ts")] {
        if let Some(v) = opts[k].as_str().filter(|v| !v.is_empty()) {
            done[api_key] = json!(v);
        }
    }
    let v = slack_ok(post_json(ctx, &api("files.completeUploadExternal"), "slackApi", c, &done).await?)?;
    Ok(v["files"][0].clone())
}

/// Telegram: file uploads from binary data and file downloads are handled here; everything else
/// (texts, URLs/file ids, chats, callbacks) comes from the generated definition.
async fn telegram(ctx: &Ctx<'_>) -> Result<Out> {
    let (resource, op) = (ctx.s("resource", 0, "message")?, ctx.s("operation", 0, "sendMessage")?);
    let field = match op.as_str() {
        "sendAnimation" => "animation",
        "sendAudio" => "audio",
        "sendDocument" => "document",
        "sendPhoto" => "photo",
        "sendSticker" => "sticker",
        "sendVideo" => "video",
        _ => "",
    };
    let upload = resource == "message" && !field.is_empty() && ctx.b("binaryData", 0, false)?;
    if !upload && !(resource == "file" && op == "get") {
        return generated(ctx).await;
    }
    let c = ctx.creds("telegramApi")?;
    let base = c["baseUrl"].as_str().filter(|s| !s.is_empty()).unwrap_or("https://api.telegram.org").trim_end_matches('/').to_string();
    let base = super::declarative::rebase(base);
    let token = as_str(&c["accessToken"]);
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        if upload {
            let prop = ctx.s("binaryPropertyName", i, "data")?;
            let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&prop)).ok_or_else(|| anyhow!("No binary property \"{prop}\""))?;
            let name = bin["fileName"].as_str().filter(|n| !n.is_empty()).unwrap_or("file").to_string();
            let part = reqwest::multipart::Part::bytes(binary_bytes(bin)?).file_name(name).mime_str(bin["mimeType"].as_str().unwrap_or("application/octet-stream"))?;
            let mut form = reqwest::multipart::Form::new().text("chat_id", ctx.s("chatId", i, "")?).part(field, part);
            for (k, v) in ctx.val("additionalFields", i, json!({}))?.as_object().into_iter().flatten() {
                if k != "appendAttribution" && !v.is_null() {
                    form = form.text(k.clone(), if v.is_string() { as_str(v) } else { v.to_string() });
                }
            }
            let resp = ctx.eng.app.http.post(format!("{base}/bot{token}/{op}")).multipart(form).timeout(Duration::from_secs(300)).send().await?;
            let v: Value = resp.json().await?;
            if v["ok"] == false {
                return Err(anyhow!("Telegram error: {}", as_str(&v["description"])));
            }
            out.push(Item::paired(v, i));
            continue;
        }
        let v = post_json(ctx, &format!("{base}/bot{token}/getFile"), "none", &json!({}), &json!({"file_id": ctx.s("fileId", i, "")?})).await?;
        if v["ok"] == false {
            return Err(anyhow!("Telegram error: {}", as_str(&v["description"])));
        }
        let file = v["result"].clone();
        let mut item = Item::paired(file.clone(), i);
        if ctx.b("download", i, true)? {
            let path = as_str(&file["file_path"]);
            let resp = ctx.eng.app.http.get(format!("{base}/file/bot{token}/{path}")).send().await?;
            if !resp.status().is_success() {
                return Err(anyhow!("Telegram file download failed with {}", resp.status()));
            }
            let name = path.rsplit('/').next().unwrap_or("file").to_string();
            let bytes = resp.bytes().await?;
            let mut bin = Map::new();
            bin.insert("data".into(), make_binary(&bytes, mime_for(&name), &name));
            item.binary = Some(bin);
        }
        out.push(item);
    }
    Ok(vec![out])
}

/// Discord: bot operations come from the generated definition; the webhook connection (and
/// workflows from before resources existed) posts through the webhook URL.
async fn discord(ctx: &Ctx<'_>) -> Result<Out> {
    let p = &ctx.node.parameters;
    let has_webhook_cred = ctx.node.credentials.as_ref().is_some_and(|c| c.get("discordWebhookApi").is_some());
    let legacy = p.get("resource").is_none() && p.get("authentication").is_none() && has_webhook_cred;
    if !(legacy || p["authentication"] == "webhook" || p["resource"] == "webhook") {
        return generated(ctx).await;
    }
    let c = ctx.creds("discordWebhookApi")?;
    let url = format!("{}?wait=true", as_str(&c["webhookUri"]));
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let mut body = json!({"content": ctx.s("content", i, "")?});
        for (k, v) in ctx.val("options", i, json!({}))?.as_object().into_iter().flatten() {
            body[k.as_str()] = v.clone();
        }
        let resp = ctx.eng.app.http.post(&url).json(&body).send().await?;
        let status = resp.status();
        let text = resp.text().await?;
        if !status.is_success() {
            return Err(anyhow!("Discord returned {}: {text}", status.as_u16()));
        }
        out.push(Item::paired(serde_json::from_str(&text).unwrap_or(json!({"success": true})), i));
    }
    Ok(vec![out])
}

fn chat_messages(ctx: &Ctx, i: usize) -> Result<Vec<Value>> {
    Ok(ctx
        .list("messages.values", i)?
        .into_iter()
        .map(|m| json!({"role": m["role"].as_str().unwrap_or("user"), "content": as_str(&m["content"])}))
        .collect())
}

async fn openai(ctx: &Ctx<'_>) -> Result<Out> {
    let c = ctx.creds("openAiApi")?;
    let base = c["url"].as_str().filter(|s| !s.is_empty()).unwrap_or("https://api.openai.com/v1").trim_end_matches('/').to_string();
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let resource = ctx.s("resource", i, "text")?;
        let op = ctx.s("operation", i, "message")?;
        if resource != "text" || op != "message" {
            return Err(anyhow!("OpenAI {resource}/{op} is not supported; use the HTTP Request node with OpenAI credentials"));
        }
        let mut body = json!({"model": rl_value(&ctx.val("modelId", i, json!("gpt-4o-mini"))?), "messages": chat_messages(ctx, i)?});
        if let Some(t) = ctx.get("options.temperature", i)? {
            body["temperature"] = t;
        }
        if let Some(t) = ctx.get("options.maxTokens", i)? {
            body["max_tokens"] = t;
        }
        if ctx.b("jsonOutput", i, false)? {
            body["response_format"] = json!({"type": "json_object"});
        }
        let v = post_json(ctx, &format!("{base}/chat/completions"), "openAiApi", &c, &body).await?;
        let item = if ctx.b("simplify", i, true)? {
            let mut choice = v["choices"][0].clone();
            if ctx.b("jsonOutput", i, false)? {
                if let Some(s) = choice["message"]["content"].as_str() {
                    choice["message"]["content"] = serde_json::from_str(s).unwrap_or(json!(s));
                }
            }
            choice
        } else {
            v
        };
        out.push(Item::paired(item, i));
    }
    Ok(vec![out])
}

async fn anthropic(ctx: &Ctx<'_>) -> Result<Out> {
    let c = ctx.creds("anthropicApi")?;
    let base = c["url"].as_str().filter(|s| !s.is_empty()).unwrap_or("https://api.anthropic.com").trim_end_matches('/').to_string();
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let mut msgs = chat_messages(ctx, i)?;
        let mut system = ctx.s("options.system", i, "")?;
        msgs.retain(|m| {
            if m["role"] == "system" {
                system.push_str(&as_str(&m["content"]));
                false
            } else {
                true
            }
        });
        let mut body = json!({
            "model": rl_value(&ctx.val("modelId", i, json!("claude-sonnet-5-5"))?),
            "max_tokens": ctx.n("options.maxTokens", i, 4096.0)? as i64,
            "messages": msgs,
        });
        if !system.is_empty() {
            body["system"] = json!(system);
        }
        if let Some(t) = ctx.get("options.temperature", i)? {
            body["temperature"] = t;
        }
        let v = post_json(ctx, &format!("{base}/v1/messages"), "anthropicApi", &c, &body).await?;
        let item = if ctx.b("simplify", i, true)? {
            let text: String = v["content"].as_array().into_iter().flatten().filter_map(|b| b["text"].as_str()).collect();
            json!({"content": text, "model": v["model"], "stop_reason": v["stop_reason"], "usage": v["usage"]})
        } else {
            v
        };
        out.push(Item::paired(item, i));
    }
    Ok(vec![out])
}

/// Publishes each item to an MQTT topic and waits until the broker has accepted it.
async fn mqtt(ctx: &Ctx<'_>) -> Result<Out> {
    let c = ctx.creds("mqtt")?;
    let (client, mut ev) = rumqttc::AsyncClient::new(crate::listeners::mqtt_options(&c, &uuid::Uuid::new_v4().simple().to_string()[..8]), 64);
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let topic = ctx.s("topic", i, "")?;
        let payload = if ctx.b("sendInputData", i, true)? { it.json.to_string() } else { ctx.s("message", i, "")? };
        let qos = crate::listeners::qos(&ctx.val("options.qos", i, json!(0))?);
        client.publish(topic, qos, ctx.b("options.retain", i, false)?, payload.into_bytes()).await?;
        // Drive the event loop until the publish is on the wire (QoS 0) or acknowledged (QoS 1/2).
        let wait = async {
            loop {
                match ev.poll().await? {
                    rumqttc::Event::Outgoing(rumqttc::Outgoing::Publish(_)) if qos == rumqttc::QoS::AtMostOnce => return Ok::<_, anyhow::Error>(()),
                    rumqttc::Event::Incoming(rumqttc::Packet::PubAck(_)) | rumqttc::Event::Incoming(rumqttc::Packet::PubComp(_)) => return Ok(()),
                    _ => {}
                }
            }
        };
        tokio::time::timeout(Duration::from_secs(20), wait).await.map_err(|_| anyhow!("MQTT broker did not confirm the message"))??;
        out.push(Item::paired(json!({"success": true}), i));
    }
    let _ = client.disconnect().await;
    let _ = tokio::time::timeout(Duration::from_secs(2), ev.poll()).await;
    Ok(vec![out])
}

async fn execute_command(ctx: &Ctx<'_>) -> Result<Out> {
    if !ctx.eng.app.cfg.enable_execute_command {
        return Err(anyhow!("The Execute Command node is disabled. Set FLOWWOLF_ENABLE_EXECUTE_COMMAND=true to enable it"));
    }
    let once = ctx.b("executeOnce", 0, true)?;
    let n = if once { 1 } else { ctx.items().len() };
    let mut out = vec![];
    for i in 0..n {
        let cmd = ctx.s("command", i, "")?;
        let o = if cfg!(windows) {
            tokio::process::Command::new("cmd").arg("/C").arg(&cmd).output().await?
        } else {
            tokio::process::Command::new("sh").arg("-c").arg(&cmd).output().await?
        };
        let code = o.status.code().unwrap_or(-1);
        let j = json!({"exitCode": code, "stdout": String::from_utf8_lossy(&o.stdout).trim_end(), "stderr": String::from_utf8_lossy(&o.stderr).trim_end()});
        if code != 0 {
            return Err(anyhow!("Command failed: {}\n{}", cmd, as_str(&j["stderr"])));
        }
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn literals_are_escaped() {
        assert_eq!(pg_lit(&json!("O'Brien")), "'O''Brien'");
        assert_eq!(pg_lit(&json!(null)), "NULL");
        assert_eq!(pg_ident("a\"b"), "\"a\"\"b\"");
        assert_eq!(urlencode("p@ss w"), "p%40ss%20w");
    }
}
