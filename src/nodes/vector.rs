//! Simple vector store: embeddings from an OpenAI-compatible API, stored in the database,
//! searched by cosine similarity. Used by the Vector Store node and the AI Agent tool.

use super::{Ctx, Out};
use crate::app::App;
use crate::model::*;
use anyhow::{Result, anyhow};
use crate::p;
use serde_json::{Value, json};
use std::time::Duration;

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    (kind == "@flowwolf/nodes.vectorStore").then_some(())?;
    Some(node(ctx).await)
}

pub(crate) fn ensure_table(app: &App) -> Result<()> {
    app.db.batch(
        "CREATE TABLE IF NOT EXISTS vectors (id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, text TEXT NOT NULL,
           metadata TEXT NOT NULL, embedding BLOB NOT NULL);
         CREATE INDEX IF NOT EXISTS vectors_store ON vectors(store);",
    )
}

/// Embeds texts with an OpenAI-compatible `/embeddings` endpoint (OpenAI, Ollama, ...).
pub async fn embed(app: &App, creds: &Value, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>> {
    let base = creds["url"].as_str().filter(|s| !s.is_empty()).unwrap_or("https://api.openai.com/v1").trim_end_matches('/').to_string();
    let mut out = vec![];
    for chunk in texts.chunks(96) {
        let r = app
            .http
            .post(format!("{base}/embeddings"))
            .bearer_auth(as_str(&creds["apiKey"]))
            .json(&json!({"model": model, "input": chunk}))
            .timeout(Duration::from_secs(120))
            .send()
            .await?;
        let status = r.status();
        let v: Value = r.json().await?;
        if !status.is_success() {
            return Err(anyhow!("Embeddings request failed ({status}): {}", as_str(&v["error"]["message"])));
        }
        let mut data = v["data"].as_array().cloned().unwrap_or_default();
        data.sort_by_key(|d| d["index"].as_u64().unwrap_or(0));
        for d in data {
            out.push(d["embedding"].as_array().ok_or_else(|| anyhow!("Embedding missing in response"))?.iter().map(|x| x.as_f64().unwrap_or(0.0) as f32).collect());
        }
    }
    if out.len() != texts.len() {
        return Err(anyhow!("Expected {} embeddings, got {}", texts.len(), out.len()));
    }
    Ok(out)
}

/// Stores one chunk with its embedding (used by the vector store cluster nodes).
pub fn insert(app: &App, store: &str, text: &str, metadata: &Value, embedding: &[f32]) -> Result<()> {
    ensure_table(app)?;
    app.db.exec("INSERT INTO vectors(store, text, metadata, embedding) VALUES(?1, ?2, ?3, ?4)", p![store, text, metadata.to_string(), to_blob(embedding)])?;
    Ok(())
}

fn to_blob(v: &[f32]) -> Vec<u8> {
    v.iter().flat_map(|f| f.to_le_bytes()).collect()
}

fn from_blob(b: &[u8]) -> Vec<f32> {
    b.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()
}

pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let (mut dot, mut na, mut nb) = (0.0f32, 0.0f32, 0.0f32);
    for (x, y) in a.iter().zip(b) {
        dot += x * y;
        na += x * x;
        nb += y * y;
    }
    if na == 0.0 || nb == 0.0 { 0.0 } else { dot / (na.sqrt() * nb.sqrt()) }
}

/// Top `k` documents of a store for a query vector.
// ponytail: brute-force scan, fine up to ~100k chunks; add an ANN index if stores get larger.
pub fn search(app: &App, store: &str, q: &[f32], k: usize) -> Result<Vec<Value>> {
    ensure_table(app)?;
    let rows: Vec<(String, String, Vec<u8>)> =
        app.db.rows("SELECT text, metadata, embedding FROM vectors WHERE store = ?1", p![store])?.into_iter().map(|r| (r[0].s(), r[1].s(), r[2].bytes())).collect();
    let mut scored: Vec<(f32, String, String)> = rows.into_iter().map(|(t, m, e)| (cosine(q, &from_blob(&e)), t, m)).collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    Ok(scored
        .into_iter()
        .take(k)
        .map(|(s, t, m)| json!({"text": t, "score": s, "metadata": serde_json::from_str::<Value>(&m).unwrap_or(Value::Null)}))
        .collect())
}

/// Splits text into chunks of about `size` characters with `overlap`.
pub fn chunk(text: &str, size: usize, overlap: usize) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    if size == 0 || chars.len() <= size {
        return vec![text.to_string()];
    }
    let step = size.saturating_sub(overlap).max(1);
    (0..chars.len()).step_by(step).map(|s| chars[s..(s + size).min(chars.len())].iter().collect::<String>()).take_while(|c| !c.is_empty()).collect()
}

async fn node(ctx: &Ctx<'_>) -> Result<Out> {
    let app = &ctx.eng.app;
    ensure_table(app)?;
    let mode = ctx.s("mode", 0, "insert")?;
    let store = ctx.s("memoryKey", 0, "default")?;
    let model = ctx.s("model", 0, "text-embedding-3-small")?;
    match mode.as_str() {
        "clear" => {
            let n = app.db.exec("DELETE FROM vectors WHERE store = ?1", p![&store])?;
            Ok(vec![vec![Item::new(json!({"store": store, "deleted": n}))]])
        }
        "insert" => {
            let creds = ctx.creds("openAiApi")?;
            let (size, overlap) = (ctx.n("options.chunkSize", 0, 0.0)? as usize, ctx.n("options.chunkOverlap", 0, 0.0)? as usize);
            let mut docs: Vec<(String, Value)> = vec![];
            for (i, it) in ctx.items().iter().enumerate() {
                let text = ctx.s("text", i, "")?;
                if text.trim().is_empty() {
                    continue;
                }
                let meta = if ctx.b("options.includeItemData", i, true)? { it.json.clone() } else { json!({}) };
                docs.extend(chunk(&text, size, overlap).into_iter().map(|c| (c, meta.clone())));
            }
            let vecs = embed(app, &creds, &model, &docs.iter().map(|d| d.0.clone()).collect::<Vec<_>>()).await?;
            for ((t, m), e) in docs.iter().zip(&vecs) {
                app.db.exec("INSERT INTO vectors(store, text, metadata, embedding) VALUES(?1, ?2, ?3, ?4)", p![&store, t, m.to_string(), to_blob(e)])?;
            }
            Ok(vec![vec![Item::new(json!({"store": store, "inserted": docs.len()}))]])
        }
        "load" => {
            let creds = ctx.creds("openAiApi")?;
            let mut out = vec![];
            for i in 0..ctx.items().len() {
                let q = ctx.s("prompt", i, "")?;
                let k = ctx.n("topK", i, 4.0)?.max(1.0) as usize;
                let qv = embed(app, &creds, &model, &[q]).await?.remove(0);
                out.extend(search(app, &store, &qv, k)?.into_iter().map(|v| Item::paired(v, i)));
            }
            Ok(vec![out])
        }
        m => Err(anyhow!("Unknown mode {m}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn math_and_chunks() {
        assert!((cosine(&[1.0, 0.0], &[1.0, 0.0]) - 1.0).abs() < 1e-6);
        assert_eq!(cosine(&[1.0, 0.0], &[0.0, 1.0]), 0.0);
        assert_eq!(from_blob(&to_blob(&[0.5, -2.0])), vec![0.5, -2.0]);
        assert_eq!(chunk("abcdefghij", 4, 1), vec!["abcd", "defg", "ghij", "j"]);
        assert_eq!(chunk("short", 100, 10), vec!["short"]);
    }
}
