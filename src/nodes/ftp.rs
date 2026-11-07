//! FTP node (protocol FTP / FTPS): list, download, upload, rename and delete files and folders.

use super::data::{binary_bytes, make_binary, mime_for};
use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use suppaftp::list::File;
use suppaftp::tokio::AsyncFtpStream;

/// One directory entry in the shape n8n returns.
fn entry(f: &File, dir: &str) -> Value {
    let kind = if f.is_directory() { "d" } else if f.is_symlink() { "l" } else { "-" };
    let modified: chrono::DateTime<chrono::Utc> = f.modified().into();
    json!({"type": kind, "name": f.name(), "size": f.size(), "modifyTime": modified.to_rfc3339(),
           "path": format!("{}/{}", dir.trim_end_matches('/'), f.name())})
}

async fn connect(ctx: &Ctx<'_>) -> Result<AsyncFtpStream> {
    let c = ctx.creds("ftp")?;
    let port = as_f64(&c["port"]).map(|p| p as u16).unwrap_or(21);
    let mut ftp = AsyncFtpStream::connect(format!("{}:{port}", as_str(&c["host"]))).await.map_err(|e| anyhow!("FTP connection failed: {e}"))?;
    // Passive data connections go to the control connection's host, not the address the server
    // announces (often a private one behind NAT or Docker).
    ftp.set_passive_nat_workaround(true);
    let user = Some(as_str(&c["username"])).filter(|u| !u.is_empty()).unwrap_or_else(|| "anonymous".into());
    ftp.login(&user, &as_str(&c["password"])).await.map_err(|e| anyhow!("FTP login failed: {e}"))?;
    ftp.transfer_type(suppaftp::types::FileType::Binary).await?;
    Ok(ftp)
}

async fn list(ftp: &mut AsyncFtpStream, dir: &str) -> Result<Vec<File>> {
    let lines = ftp.list(Some(dir)).await.map_err(|e| anyhow!("Listing {dir} failed: {e}"))?;
    Ok(lines.iter().filter_map(|l| File::try_from(l.as_str()).ok()).filter(|f| f.name() != "." && f.name() != "..").collect())
}

/// Creates every missing folder of `path` (the last segment is the file name).
async fn make_dirs(ftp: &mut AsyncFtpStream, path: &str) {
    let parts: Vec<&str> = path.split('/').collect();
    let mut cur = String::new();
    for p in &parts[..parts.len().saturating_sub(1)] {
        if p.is_empty() {
            continue;
        }
        cur = format!("{cur}/{p}");
        let _ = ftp.mkdir(&cur).await;
    }
}

async fn remove_tree(ftp: &mut AsyncFtpStream, dir: &str) -> Result<()> {
    let mut stack = vec![(dir.to_string(), false)];
    while let Some((d, listed)) = stack.pop() {
        if listed {
            ftp.rmdir(&d).await.map_err(|e| anyhow!("Removing {d} failed: {e}"))?;
            continue;
        }
        stack.push((d.clone(), true));
        for f in list(ftp, &d).await? {
            let p = format!("{}/{}", d.trim_end_matches('/'), f.name());
            if f.is_directory() {
                stack.push((p, false));
            } else {
                ftp.rm(&p).await.map_err(|e| anyhow!("Deleting {p} failed: {e}"))?;
            }
        }
    }
    Ok(())
}

pub async fn run(ctx: &Ctx<'_>) -> Result<Out> {
    if ctx.s("protocol", 0, "ftp")? == "sftp" {
        return super::sftp::run(ctx).await;
    }
    let mut ftp = connect(ctx).await?;
    let op = ctx.s("operation", 0, "download")?;
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let o = ctx.val("options", i, json!({}))?;
        match op.as_str() {
            "list" => {
                let root = ctx.s("path", i, "/")?;
                let recursive = ctx.b("recursive", i, false)?;
                let mut dirs = vec![root];
                while let Some(d) = dirs.pop() {
                    for f in list(&mut ftp, &d).await? {
                        if recursive && f.is_directory() {
                            dirs.push(format!("{}/{}", d.trim_end_matches('/'), f.name()));
                        }
                        out.push(Item::paired(entry(&f, &d), i));
                    }
                }
            }
            "download" => {
                let path = ctx.s("path", i, "")?;
                let mut stream = ftp.retr_as_stream(&path).await.map_err(|e| anyhow!("Downloading {path} failed: {e}"))?;
                let mut data = vec![];
                tokio::io::AsyncReadExt::read_to_end(&mut stream, &mut data).await?;
                stream.finish().await.map_err(|e| anyhow!("Downloading {path} failed: {e}"))?;
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
                make_dirs(&mut ftp, &path).await;
                let mut reader = std::io::Cursor::new(data);
                ftp.put_file(&path, &mut reader).await.map_err(|e| anyhow!("Uploading {path} failed: {e}"))?;
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
                    make_dirs(&mut ftp, &to).await;
                }
                ftp.rename(&from, &to).await.map_err(|e| anyhow!("Renaming {from} failed: {e}"))?;
                out.push(Item::paired(json!({"success": true}), i));
            }
            "delete" => {
                let path = ctx.s("path", i, "")?;
                if o["folder"] == true {
                    if o["recursive"] == true {
                        remove_tree(&mut ftp, &path).await?;
                    } else {
                        ftp.rmdir(&path).await.map_err(|e| anyhow!("Removing {path} failed: {e}"))?;
                    }
                } else {
                    ftp.rm(&path).await.map_err(|e| anyhow!("Deleting {path} failed: {e}"))?;
                }
                out.push(Item::paired(json!({"success": true}), i));
            }
            o => return Err(anyhow!("FTP operation \"{o}\" is not supported")),
        }
    }
    let _ = ftp.quit().await;
    Ok(vec![out])
}
