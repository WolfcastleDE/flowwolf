//! Minimal IMAP client for the Email Trigger: LOGIN, SELECT, UID SEARCH/FETCH/STORE, LOGOUT.

use crate::model::*;
use anyhow::{Result, anyhow};
use mail_parser::MimeHeaders;
use serde_json::{Map, Value, json};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

trait Stream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Stream for T {}

pub struct Imap {
    io: BufReader<Box<dyn Stream>>,
    tag: u32,
}

fn quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

const IO_TIMEOUT: Duration = Duration::from_secs(60);

impl Imap {
    pub async fn connect(host: &str, port: u16, secure: bool, insecure: bool) -> Result<Imap> {
        let tcp = tokio::time::timeout(Duration::from_secs(20), tokio::net::TcpStream::connect((host, port))).await.map_err(|_| anyhow!("IMAP connect timed out"))??;
        let stream: Box<dyn Stream> = if secure {
            let builder = rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider())).with_safe_default_protocol_versions()?;
            let cfg = if insecure {
                builder.dangerous().with_custom_certificate_verifier(Arc::new(crate::nodes::AcceptAnyCert)).with_no_client_auth()
            } else {
                builder.with_root_certificates(rustls::RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() }).with_no_client_auth()
            };
            let name = rustls::pki_types::ServerName::try_from(host.to_string())?;
            Box::new(tokio_rustls::TlsConnector::from(Arc::new(cfg)).connect(name, tcp).await.map_err(|e| anyhow!("IMAP TLS handshake failed: {e}"))?)
        } else {
            Box::new(tcp)
        };
        let mut c = Imap { io: BufReader::new(stream), tag: 0 };
        let mut greet = String::new();
        tokio::time::timeout(IO_TIMEOUT, c.io.read_line(&mut greet)).await.map_err(|_| anyhow!("IMAP greeting timed out"))??;
        if !greet.starts_with("* OK") && !greet.starts_with("* PREAUTH") {
            return Err(anyhow!("Unexpected IMAP greeting: {}", greet.trim()));
        }
        Ok(c)
    }

    /// Sends a command and collects untagged lines plus any literals ({n} blocks).
    async fn cmd(&mut self, cmd: &str) -> Result<(Vec<String>, Vec<Vec<u8>>)> {
        self.tag += 1;
        let tag = format!("a{}", self.tag);
        let w = self.io.get_mut();
        w.write_all(format!("{tag} {cmd}\r\n").as_bytes()).await?;
        w.flush().await?;
        let (mut lines, mut literals) = (vec![], vec![]);
        loop {
            let mut line = String::new();
            let n = tokio::time::timeout(IO_TIMEOUT, self.io.read_line(&mut line)).await.map_err(|_| anyhow!("IMAP read timed out"))??;
            if n == 0 {
                return Err(anyhow!("IMAP connection closed"));
            }
            let t = line.trim_end();
            if t.ends_with('}') {
                if let Some(size) = t.rfind('{').and_then(|p| t[p + 1..t.len() - 1].trim_end_matches('+').parse::<usize>().ok()) {
                    let mut buf = vec![0u8; size];
                    tokio::time::timeout(IO_TIMEOUT, self.io.read_exact(&mut buf)).await.map_err(|_| anyhow!("IMAP read timed out"))??;
                    literals.push(buf);
                    lines.push(t.to_string());
                    continue;
                }
            }
            if let Some(rest) = t.strip_prefix(&format!("{tag} ")) {
                if rest.starts_with("OK") {
                    return Ok((lines, literals));
                }
                let verb = cmd.split_whitespace().next().unwrap_or("");
                return Err(anyhow!("IMAP {verb} failed: {rest}"));
            }
            lines.push(t.to_string());
        }
    }

    pub async fn login(&mut self, user: &str, pass: &str) -> Result<()> {
        self.cmd(&format!("LOGIN {} {}", quote(user), quote(pass))).await.map(|_| ())
    }

    pub async fn select(&mut self, mailbox: &str) -> Result<()> {
        self.cmd(&format!("SELECT {}", quote(mailbox))).await.map(|_| ())
    }

    pub async fn search(&mut self, criteria: &str) -> Result<Vec<u32>> {
        let (lines, _) = self.cmd(&format!("UID SEARCH {criteria}")).await?;
        Ok(lines.iter().filter_map(|l| l.strip_prefix("* SEARCH")).flat_map(|r| r.split_whitespace().filter_map(|n| n.parse().ok())).collect())
    }

    pub async fn fetch(&mut self, uid: u32) -> Result<Option<Vec<u8>>> {
        let (_, mut lits) = self.cmd(&format!("UID FETCH {uid} (BODY.PEEK[])")).await?;
        Ok(if lits.is_empty() { None } else { Some(lits.remove(0)) })
    }

    pub async fn mark_seen(&mut self, uid: u32) -> Result<()> {
        self.cmd(&format!("UID STORE {uid} +FLAGS (\\Seen)")).await.map(|_| ())
    }

    pub async fn logout(&mut self) {
        let _ = self.cmd("LOGOUT").await;
    }
}

fn addresses(a: Option<&mail_parser::Address>) -> String {
    a.map(|a| {
        a.iter()
            .map(|x| match (x.name(), x.address()) {
                (Some(n), Some(e)) => format!("{n} <{e}>"),
                (None, Some(e)) => e.to_string(),
                (Some(n), None) => n.to_string(),
                _ => String::new(),
            })
            .collect::<Vec<_>>()
            .join(", ")
    })
    .unwrap_or_default()
}

/// Turns a raw RFC 822 message into an item ("simple" format of the n8n Email Trigger).
pub fn to_item(raw: &[u8], format: &str, attachments: bool) -> Result<Item> {
    if format == "raw" {
        return Ok(Item::new(json!({"raw": String::from_utf8_lossy(raw)})));
    }
    let m = mail_parser::MessageParser::default().parse(raw).ok_or_else(|| anyhow!("Cannot parse email"))?;
    let mut j = json!({
        "from": addresses(m.from()),
        "to": addresses(m.to()),
        "cc": addresses(m.cc()),
        "subject": m.subject().unwrap_or(""),
        "date": m.date().map(|d| d.to_rfc3339()),
        "messageId": m.message_id(),
        "textPlain": m.body_text(0).map(|t| t.to_string()),
        "textHtml": m.body_html(0).map(|t| t.to_string()),
    });
    if format == "resolved" {
        j["headers"] = Value::Object(m.headers().iter().map(|h| (h.name().to_string(), json!(format!("{:?}", h.value())))).collect());
    }
    let mut bin = Map::new();
    if attachments {
        for (n, a) in m.attachments().enumerate() {
            let name = a.attachment_name().unwrap_or("attachment").to_string();
            let mime = a.content_type().map(|c| format!("{}/{}", c.ctype(), c.subtype().unwrap_or("octet-stream"))).unwrap_or_else(|| "application/octet-stream".into());
            bin.insert(format!("attachment_{n}"), crate::nodes::make_binary(a.contents(), &mime, &name));
        }
    }
    Ok(Item { json: j, binary: (!bin.is_empty()).then_some(bin), paired_item: None })
}

/// Fetches matching messages. `skip` holds UIDs already processed (for "do nothing" post-processing).
pub async fn poll(creds: &Value, params: &Value, skip: &mut std::collections::HashSet<u32>) -> Result<Vec<Item>> {
    let host = as_str(&creds["host"]);
    let port = as_f64(&creds["port"]).unwrap_or(993.0) as u16;
    let secure = creds["secure"].as_bool().unwrap_or(true);
    let mut c = Imap::connect(&host, port, secure, creds["allowUnauthorizedCerts"] == true).await?;
    c.login(&as_str(&creds["user"]), &as_str(&creds["password"])).await?;
    c.select(params["mailbox"].as_str().filter(|m| !m.is_empty()).unwrap_or("INBOX")).await?;
    let criteria = match &params["options"]["customEmailConfig"] {
        Value::String(s) if !s.trim().is_empty() => serde_json::from_str::<Vec<String>>(s).map(|v| v.join(" ")).unwrap_or_else(|_| s.clone()),
        _ => "UNSEEN".to_string(),
    };
    let mark_read = params["postProcessAction"].as_str().unwrap_or("read") == "read";
    let format = params["format"].as_str().unwrap_or("simple");
    let attachments = params["downloadAttachments"].as_bool().unwrap_or(false);
    let mut out = vec![];
    for uid in c.search(&criteria).await? {
        if skip.contains(&uid) {
            continue;
        }
        let Some(raw) = c.fetch(uid).await? else { continue };
        out.push(to_item(&raw, format, attachments)?);
        if mark_read {
            c.mark_seen(uid).await?;
        } else {
            skip.insert(uid);
        }
    }
    c.logout().await;
    Ok(out)
}

#[cfg(test)]
mod tests {
    #[test]
    fn parses_mail() {
        let raw = b"From: Ada <ada@example.com>\r\nTo: bob@example.com\r\nSubject: Hi there\r\nMessage-ID: <1@x>\r\nContent-Type: text/plain\r\n\r\nHello Bob\r\n";
        let it = super::to_item(raw, "simple", false).unwrap();
        assert_eq!(it.json["from"], "Ada <ada@example.com>");
        assert_eq!(it.json["subject"], "Hi there");
        assert_eq!(it.json["textPlain"].as_str().unwrap().trim(), "Hello Bob");
        assert_eq!(super::quote("a\"b"), "\"a\\\"b\"");
    }
}
