//! FTP node with protocol SFTP: the same operations over SSH (password or private key).

use super::data::{binary_bytes, make_binary, mime_for};
use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use russh_sftp::client::SftpSession;
use serde_json::{Value, json};
use std::sync::Arc;

struct Client;

impl russh::client::Handler for Client {
    type Error = russh::Error;

    // ponytail: like n8n's FTP node, the server key is not pinned; add known-hosts pinning if needed.
    async fn check_server_key(&mut self, _key: &russh::keys::PublicKeyOrCertificate) -> Result<bool, Self::Error> {
        Ok(true)
    }
}

async fn connect(ctx: &Ctx<'_>) -> Result<(russh::client::Handle<Client>, SftpSession)> {
    let c = ctx.creds("sftp")?;
    let port = as_f64(&c["port"]).map(|p| p as u16).unwrap_or(22);
    let host = as_str(&c["host"]);
    let mut session = russh::client::connect(Arc::new(russh::client::Config::default()), (host.as_str(), port), Client).await
        .map_err(|e| anyhow!("SFTP connection to {host}:{port} failed: {e}"))?;
    let user = as_str(&c["username"]);
    let key = as_str(&c["privateKey"]);
    let ok = if !key.trim().is_empty() {
        let pass = as_str(&c["passphrase"]);
        let k = russh::keys::decode_secret_key(&key, if pass.is_empty() { None } else { Some(&pass) }).map_err(|e| anyhow!("Private key could not be read: {e}"))?;
        let hash = session.best_supported_rsa_hash().await?.flatten();
        session.authenticate_publickey(&user, russh::keys::PrivateKeyWithHashAlg::new(Arc::new(k), hash)).await?.success()
    } else {
        session.authenticate_password(&user, &as_str(&c["password"])).await?.success()
    };
    if !ok {
        return Err(anyhow!("SFTP login failed for {user}"));
    }
    let channel = session.channel_open_session().await?;
    channel.request_subsystem(true, "sftp").await?;
    let sftp = SftpSession::new(channel.into_stream()).await.map_err(|e| anyhow!("SFTP subsystem failed: {e}"))?;
    Ok((session, sftp))
}

fn entry(name: &str, meta: &russh_sftp::protocol::FileAttributes, dir: &str) -> Value {
    let kind = if meta.is_dir() { "d" } else if meta.is_symlink() { "l" } else { "-" };
    let ts = |t: Option<u32>| t.and_then(|t| chrono::DateTime::from_timestamp(t as i64, 0)).map(|d| d.to_rfc3339());
    json!({"type": kind, "name": name, "size": meta.size.unwrap_or(0), "modifyTime": ts(meta.mtime), "accessTime": ts(meta.atime),
           "rights": format!("{:o}", meta.permissions.unwrap_or(0) & 0o777), "path": format!("{}/{name}", dir.trim_end_matches('/'))})
}

async fn list(sftp: &SftpSession, dir: &str) -> Result<Vec<(String, russh_sftp::protocol::FileAttributes)>> {
    Ok(sftp.read_dir(dir).await.map_err(|e| anyhow!("Listing {dir} failed: {e}"))?
        .map(|e| (e.file_name(), e.metadata()))
        .filter(|(n, _)| n != "." && n != "..")
        .collect())
}

async fn make_dirs(sftp: &SftpSession, path: &str) {
    let parts: Vec<&str> = path.split('/').collect();
    let mut cur = String::new();
    for p in &parts[..parts.len().saturating_sub(1)] {
        if p.is_empty() {
            continue;
        }
        cur = format!("{cur}/{p}");
        if !sftp.try_exists(&cur).await.unwrap_or(false) {
            let _ = sftp.create_dir(&cur).await;
        }
    }
}

async fn remove_tree(sftp: &SftpSession, dir: &str) -> Result<()> {
    let mut stack = vec![(dir.to_string(), false)];
    while let Some((d, listed)) = stack.pop() {
        if listed {
            sftp.remove_dir(&d).await.map_err(|e| anyhow!("Removing {d} failed: {e}"))?;
            continue;
        }
        stack.push((d.clone(), true));
        for (name, meta) in list(sftp, &d).await? {
            let p = format!("{}/{name}", d.trim_end_matches('/'));
            if meta.is_dir() {
                stack.push((p, false));
            } else {
                sftp.remove_file(&p).await.map_err(|e| anyhow!("Deleting {p} failed: {e}"))?;
            }
        }
    }
    Ok(())
}

pub async fn run(ctx: &Ctx<'_>) -> Result<Out> {
    let (_session, sftp) = connect(ctx).await?;
    let op = ctx.s("operation", 0, "download")?;
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let o = ctx.val("options", i, json!({}))?;
        match op.as_str() {
            "list" => {
                let recursive = ctx.b("recursive", i, false)?;
                let mut dirs = vec![ctx.s("path", i, "/")?];
                while let Some(d) = dirs.pop() {
                    for (name, meta) in list(&sftp, &d).await? {
                        if recursive && meta.is_dir() {
                            dirs.push(format!("{}/{name}", d.trim_end_matches('/')));
                        }
                        out.push(Item::paired(entry(&name, &meta, &d), i));
                    }
                }
            }
            "download" => {
                let path = ctx.s("path", i, "")?;
                let data = sftp.read(&path).await.map_err(|e| anyhow!("Downloading {path} failed: {e}"))?;
                let name = path.rsplit('/').next().unwrap_or("file").to_string();
                let mut item = ctx.items()[i].clone();
                item.paired_item = Some(json!({"item": i}));
                let mut bin = item.binary.take().unwrap_or_default();
                bin.insert(ctx.s("binaryPropertyName", i, "data")?, make_binary(&data, mime_for(&name), &name));
                item.binary = Some(bin);
                out.push(item);
            }
            "upload" => {
                let path = ctx.s("path", i, "")?;
                let data = if ctx.b("binaryData", i, true)? {
                    let field = ctx.s("binaryPropertyName", i, "data")?;
                    let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                    binary_bytes(bin)?
                } else {
                    ctx.s("fileContent", i, "")?.into_bytes()
                };
                make_dirs(&sftp, &path).await;
                // create() truncates or creates the file; write() alone only opens existing files.
                let mut file = sftp.create(&path).await.map_err(|e| anyhow!("Uploading {path} failed: {e}"))?;
                tokio::io::AsyncWriteExt::write_all(&mut file, &data).await.map_err(|e| anyhow!("Uploading {path} failed: {e}"))?;
                tokio::io::AsyncWriteExt::shutdown(&mut file).await?;
                let mut v = ctx.items()[i].json.clone();
                if !v.is_object() {
                    v = json!({});
                }
                v["success"] = json!(true);
                out.push(Item::paired(v, i));
            }
            "rename" => {
                let (from, to) = (ctx.s("oldPath", i, "")?, ctx.s("newPath", i, "")?);
                if o["createDirectories"] == true {
                    make_dirs(&sftp, &to).await;
                }
                sftp.rename(&from, &to).await.map_err(|e| anyhow!("Renaming {from} failed: {e}"))?;
                out.push(Item::paired(json!({"success": true}), i));
            }
            "delete" => {
                let path = ctx.s("path", i, "")?;
                if o["folder"] == true {
                    if o["recursive"] == true {
                        remove_tree(&sftp, &path).await?;
                    } else {
                        sftp.remove_dir(&path).await.map_err(|e| anyhow!("Removing {path} failed: {e}"))?;
                    }
                } else {
                    sftp.remove_file(&path).await.map_err(|e| anyhow!("Deleting {path} failed: {e}"))?;
                }
                out.push(Item::paired(json!({"success": true}), i));
            }
            o => return Err(anyhow!("SFTP operation \"{o}\" is not supported")),
        }
    }
    let _ = sftp.close().await;
    Ok(vec![out])
}
