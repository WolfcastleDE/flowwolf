//! n8n's AI cluster nodes: a root node (AI Agent, chains, vector stores) uses sub-nodes connected
//! through `ai_*` connections (`ai_languageModel`, `ai_memory`, `ai_tool`, `ai_outputParser`,
//! `ai_embedding`, `ai_document`, `ai_textSplitter`, `ai_vectorStore`, `ai_retriever`).
//! Sub-nodes never run on their own; the root reads their parameters and credentials in the
//! context of the item it is processing, like n8n does.

use super::{Ctx, Out, rl_value};
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::time::Duration;

const LC: &str = "@n8n/n8n-nodes-langchain.";

fn short(kind: &str) -> &str {
    kind.strip_prefix(LC).unwrap_or(kind)
}

/// Sub-nodes connected to `ctx.node` through connections of type `kind`.
pub fn subs<'a>(ctx: &Ctx<'a>, kind: &str) -> Vec<&'a Node> {
    let wf: &'a Workflow = &ctx.eng.wf;
    let mut out = vec![];
    for (src, m) in wf.connections.as_object().into_iter().flatten() {
        for list in m[kind].as_array().into_iter().flatten() {
            for c in list.as_array().into_iter().flatten() {
                if c["node"] == ctx.node.name.as_str() {
                    if let Some(n) = wf.node(src).filter(|n| !n.disabled) {
                        out.push(n);
                    }
                }
            }
        }
    }
    out
}

/// A context for a sub-node that evaluates its expressions against the root's input.
pub fn sub_ctx<'a>(ctx: &Ctx<'a>, node: &'a Node) -> Ctx<'a> {
    Ctx { eng: ctx.eng, node, inputs: ctx.inputs.clone() }
}

fn rl(ctx: &Ctx, name: &str, i: usize, default: &str) -> Result<String> {
    let v = rl_value(&ctx.val(name, i, Value::Null)?);
    Ok(if v.is_empty() { default.to_string() } else { v })
}

// ---------------------------------------------------------------------------
// language models
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
pub enum Api {
    OpenAi,
    Anthropic,
    Bedrock,
}

pub struct Model {
    pub api: Api,
    pub url: String,
    pub key: String,
    pub model: String,
    pub temperature: Option<f64>,
    pub max_tokens: Option<i64>,
    pub headers: Vec<(String, String)>,
    pub query: Vec<(String, String)>,
    /// AWS credential for Bedrock.
    pub aws: Value,
}

fn openai_like(c: &Value, url: &str, key: &str, model: String, o: &Value) -> Model {
    Model {
        api: Api::OpenAi,
        url: url.trim_end_matches('/').to_string(),
        key: key.to_string(),
        model,
        temperature: as_f64(&o["temperature"]),
        max_tokens: as_f64(&o["maxTokens"]).or_else(|| as_f64(&o["maxTokensToSample"])).filter(|n| *n > 0.0).map(|n| n as i64),
        headers: vec![],
        query: vec![],
        aws: c.clone(),
    }
}

fn cred_url(c: &Value, field: &str, default: &str) -> String {
    c[field].as_str().filter(|s| !s.is_empty()).unwrap_or(default).trim_end_matches('/').to_string()
}

/// Builds the model a chat-model sub-node describes.
pub fn model_of(sub: &Ctx, i: usize) -> Result<Model> {
    let o = sub.val("options", i, json!({}))?;
    let k = short(&sub.node.kind);
    Ok(match k {
        "lmChatOpenAi" | "lmOpenAi" => {
            let c = sub.creds("openAiApi")?;
            openai_like(&c, &cred_url(&c, "url", "https://api.openai.com/v1"), &as_str(&c["apiKey"]), rl(sub, "model", i, "gpt-4o-mini")?, &o)
        }
        "lmChatAnthropic" => {
            let c = sub.creds("anthropicApi")?;
            let mut m = openai_like(&c, &cred_url(&c, "url", "https://api.anthropic.com"), &as_str(&c["apiKey"]), rl(sub, "model", i, "claude-sonnet-5-5")?, &o);
            m.api = Api::Anthropic;
            m
        }
        "lmChatOllama" | "lmOllama" => {
            let c = sub.creds("ollamaApi")?;
            openai_like(&c, &format!("{}/v1", cred_url(&c, "baseUrl", "http://localhost:11434")), "", rl(sub, "model", i, "llama3.2")?, &o)
        }
        "lmChatMistralCloud" => {
            let c = sub.creds("mistralCloudApi")?;
            openai_like(&c, "https://api.mistral.ai/v1", &as_str(&c["apiKey"]), rl(sub, "model", i, "mistral-small-latest")?, &o)
        }
        "lmChatGroq" => {
            let c = sub.creds("groqApi")?;
            openai_like(&c, "https://api.groq.com/openai/v1", &as_str(&c["apiKey"]), rl(sub, "model", i, "llama-3.3-70b-versatile")?, &o)
        }
        "lmChatGoogleGemini" => {
            let c = sub.creds("googlePalmApi")?;
            let model = rl(sub, "modelName", i, "models/gemini-2.0-flash")?;
            openai_like(&c, &format!("{}/v1beta/openai", cred_url(&c, "host", "https://generativelanguage.googleapis.com")), &as_str(&c["apiKey"]),
                        model.trim_start_matches("models/").to_string(), &o)
        }
        "lmChatDeepSeek" => {
            let c = sub.creds("deepSeekApi")?;
            openai_like(&c, &cred_url(&c, "url", "https://api.deepseek.com"), &as_str(&c["apiKey"]), rl(sub, "model", i, "deepseek-chat")?, &o)
        }
        "lmChatOpenRouter" => {
            let c = sub.creds("openRouterApi")?;
            openai_like(&c, &cred_url(&c, "url", "https://openrouter.ai/api/v1"), &as_str(&c["apiKey"]), rl(sub, "model", i, "openai/gpt-4o-mini")?, &o)
        }
        "lmChatXAiGrok" => {
            let c = sub.creds("xAiApi")?;
            openai_like(&c, &cred_url(&c, "url", "https://api.x.ai/v1"), &as_str(&c["apiKey"]), rl(sub, "model", i, "grok-3-mini")?, &o)
        }
        "lmChatCohere" => {
            let c = sub.creds("cohereApi")?;
            openai_like(&c, &cred_url(&c, "url", "https://api.cohere.ai/compatibility/v1"), &as_str(&c["apiKey"]), rl(sub, "model", i, "command-a-03-2025")?, &o)
        }
        "lmChatAzureOpenAi" => {
            let c = sub.creds("azureOpenAiApi")?;
            let deployment = rl(sub, "model", i, "")?;
            let base = c["endpoint"].as_str().filter(|s| !s.is_empty()).map(|s| s.trim_end_matches('/').to_string())
                .unwrap_or_else(|| format!("https://{}.openai.azure.com", as_str(&c["resourceName"])));
            let mut m = openai_like(&c, &format!("{base}/openai/deployments/{deployment}"), "", deployment.clone(), &o);
            m.headers.push(("api-key".into(), as_str(&c["apiKey"])));
            m.query.push(("api-version".into(), c["apiVersion"].as_str().filter(|s| !s.is_empty()).unwrap_or("2024-10-21").to_string()));
            m
        }
        "lmChatAwsBedrock" => {
            let c = sub.creds("aws")?;
            let region = c["region"].as_str().filter(|s| !s.is_empty()).unwrap_or("us-east-1").to_string();
            let mut m = openai_like(&c, &format!("https://bedrock-runtime.{region}.amazonaws.com"), "", rl(sub, "model", i, "anthropic.claude-3-5-haiku-20241022-v1:0")?, &o);
            m.api = Api::Bedrock;
            m
        }
        other => return Err(anyhow!("'{other}' is not a chat model")),
    })
}

/// The model of a root node: its `ai_languageModel` sub-node.
pub fn model(ctx: &Ctx, i: usize) -> Result<Option<Model>> {
    match subs(ctx, "ai_languageModel").first() {
        Some(n) => model_of(&sub_ctx(ctx, n), i).map(Some),
        None => Ok(None),
    }
}

pub fn require_model(ctx: &Ctx, i: usize) -> Result<Model> {
    model(ctx, i)?.ok_or_else(|| anyhow!("Connect a chat model to \"{}\"", ctx.node.name))
}

/// A tool as offered to the model.
pub struct Tool {
    pub name: String,
    pub description: String,
    pub schema: Value,
    pub run: ToolRun,
}

pub enum ToolRun {
    /// Built into the AI Agent node's own "Tools" parameter (Flowwolf's simple agent).
    Legacy(Value),
    /// A tool sub-node.
    Node(Node),
    /// One tool of an MCP server.
    Mcp { endpoint: String, headers: Vec<(String, String)>, sse: bool, tool: String },
}

pub fn input_schema() -> Value {
    json!({"type": "object", "properties": {"input": {"type": "string", "description": "Input for the tool"}}, "required": ["input"]})
}

/// Message in the API's own format.
fn bedrock_content(v: &Value) -> Value {
    match v {
        Value::String(s) => json!([{"text": s}]),
        other => other.clone(),
    }
}

/// One chat turn. Returns (assistant message to append, tool calls (id, name, args), text).
pub async fn chat(app: &crate::app::AppState, m: &Model, system: &str, msgs: &[Value], tools: &[Tool]) -> Result<(Value, Vec<(String, String, Value)>, String)> {
    let http = &app.http;
    match m.api {
        Api::Anthropic => {
            let mut body = json!({"model": m.model, "max_tokens": m.max_tokens.unwrap_or(4096), "messages": msgs});
            if !system.is_empty() {
                body["system"] = json!(system);
            }
            if !tools.is_empty() {
                body["tools"] = Value::Array(tools.iter().map(|t| json!({"name": t.name, "description": t.description, "input_schema": t.schema})).collect());
            }
            if let Some(t) = m.temperature {
                body["temperature"] = json!(t);
            }
            let r = http.post(format!("{}/v1/messages", m.url)).header("x-api-key", &m.key).header("anthropic-version", "2023-06-01").json(&body).timeout(Duration::from_secs(300)).send().await?;
            let status = r.status();
            let v: Value = r.json().await?;
            if !status.is_success() {
                return Err(anyhow!("Anthropic returned {status}: {}", as_str(&v["error"]["message"])));
            }
            let blocks = v["content"].as_array().cloned().unwrap_or_default();
            let calls = blocks.iter().filter(|b| b["type"] == "tool_use").map(|b| (as_str(&b["id"]), as_str(&b["name"]), b["input"].clone())).collect();
            let text = blocks.iter().filter_map(|b| b["text"].as_str()).collect::<Vec<_>>().join("");
            Ok((json!({"role": "assistant", "content": blocks}), calls, text))
        }
        Api::Bedrock => {
            let messages: Vec<Value> = msgs.iter().map(|x| json!({"role": x["role"], "content": bedrock_content(&x["content"])})).collect();
            let mut body = json!({"messages": messages, "inferenceConfig": {"maxTokens": m.max_tokens.unwrap_or(4096)}});
            if let Some(t) = m.temperature {
                body["inferenceConfig"]["temperature"] = json!(t);
            }
            if !system.is_empty() {
                body["system"] = json!([{"text": system}]);
            }
            if !tools.is_empty() {
                body["toolConfig"] = json!({"tools": tools.iter().map(|t| json!({"toolSpec": {"name": t.name, "description": t.description, "inputSchema": {"json": t.schema}}})).collect::<Vec<_>>()});
            }
            let url = format!("{}/model/{}/converse", super::declarative::rebase(m.url.clone()), urlenc(&m.model));
            let mut req = http.post(url).json(&body).timeout(Duration::from_secs(300)).build()?;
            super::declarative::sign_v4(&mut req, &m.aws, "bedrock", chrono::Utc::now())?;
            let r = http.execute(req).await?;
            let status = r.status();
            let v: Value = r.json().await?;
            if !status.is_success() {
                return Err(anyhow!("Bedrock returned {status}: {}", as_str(&v["message"])));
            }
            let content = v["output"]["message"]["content"].as_array().cloned().unwrap_or_default();
            let calls = content.iter().filter_map(|b| b.get("toolUse")).map(|u| (as_str(&u["toolUseId"]), as_str(&u["name"]), u["input"].clone())).collect();
            let text = content.iter().filter_map(|b| b["text"].as_str()).collect::<Vec<_>>().join("");
            Ok((json!({"role": "assistant", "content": content}), calls, text))
        }
        Api::OpenAi => {
            let mut all = vec![];
            if !system.is_empty() {
                all.push(json!({"role": "system", "content": system}));
            }
            all.extend(msgs.iter().cloned());
            let mut body = json!({"model": m.model, "messages": all});
            if !tools.is_empty() {
                body["tools"] = Value::Array(tools.iter().map(|t| json!({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}})).collect());
            }
            if let Some(t) = m.temperature {
                body["temperature"] = json!(t);
            }
            if let Some(n) = m.max_tokens {
                body["max_tokens"] = json!(n);
            }
            let mut rb = http.post(format!("{}/chat/completions", super::declarative::rebase(m.url.clone()))).json(&body).query(&m.query).timeout(Duration::from_secs(300));
            if !m.key.is_empty() {
                rb = rb.bearer_auth(&m.key);
            }
            for (k, v) in &m.headers {
                rb = rb.header(k, v);
            }
            let r = rb.send().await?;
            let status = r.status();
            let v: Value = r.json().await?;
            if !status.is_success() {
                return Err(anyhow!("The model API returned {status}: {}", as_str(&v["error"]["message"])));
            }
            let msg = v["choices"][0]["message"].clone();
            let calls = msg["tool_calls"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| (as_str(&c["id"]), as_str(&c["function"]["name"]), serde_json::from_str(c["function"]["arguments"].as_str().unwrap_or("{}")).unwrap_or(json!({}))))
                .collect();
            Ok((msg.clone(), calls, as_str(&msg["content"])))
        }
    }
}

/// Tool results in the API's format.
pub fn tool_results(api: Api, results: Vec<(String, String)>) -> Vec<Value> {
    match api {
        Api::Anthropic => vec![json!({"role": "user", "content": results.into_iter().map(|(id, out)| json!({"type": "tool_result", "tool_use_id": id, "content": out})).collect::<Vec<_>>()})],
        Api::Bedrock => vec![json!({"role": "user", "content": results.into_iter().map(|(id, out)| json!({"toolResult": {"toolUseId": id, "content": [{"text": out}]}})).collect::<Vec<_>>()})],
        Api::OpenAi => results.into_iter().map(|(id, out)| json!({"role": "tool", "tool_call_id": id, "content": out})).collect(),
    }
}

/// Single prompt, plain text answer (chains).
pub async fn complete(ctx: &Ctx<'_>, m: &Model, system: &str, prompt: &str) -> Result<String> {
    Ok(chat(&ctx.eng.app, m, system, &[json!({"role": "user", "content": prompt})], &[]).await?.2)
}

fn urlenc(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

/// First JSON value in a model answer (tolerates ```json fences and prose around it).
pub fn parse_json(text: &str) -> Result<Value> {
    let t = text.trim();
    let t = t.strip_prefix("```json").or_else(|| t.strip_prefix("```")).map(|x| x.trim_end_matches("```")).unwrap_or(t).trim();
    if let Ok(v) = serde_json::from_str(t) {
        return Ok(v);
    }
    let start = t.find(['{', '[']).ok_or_else(|| anyhow!("The model did not answer with JSON: {}", t.chars().take(200).collect::<String>()))?;
    let end = t.rfind(['}', ']']).filter(|e| *e >= start).ok_or_else(|| anyhow!("The model did not answer with JSON"))?;
    serde_json::from_str(&t[start..=end]).map_err(|e| anyhow!("The model's JSON is invalid: {e}"))
}

// ---------------------------------------------------------------------------
// output parsers
// ---------------------------------------------------------------------------

pub struct Parser<'a> {
    node: &'a Node,
    ctx: Ctx<'a>,
}

pub fn parser<'a>(ctx: &Ctx<'a>) -> Option<Parser<'a>> {
    subs(ctx, "ai_outputParser").first().map(|n| Parser { node: n, ctx: sub_ctx(ctx, n) })
}

impl<'a> Parser<'a> {
    fn inner(&self) -> Option<Parser<'a>> {
        parser(&self.ctx)
    }

    /// Format instructions appended to the prompt.
    pub fn instructions(&self, i: usize) -> Result<String> {
        Ok(match short(&self.node.kind) {
            "outputParserStructured" => {
                if self.ctx.s("schemaType", i, "fromJson")? == "manual" {
                    format!("Answer only with JSON that matches this JSON schema, no other text:\n{}", self.ctx.s("inputSchema", i, "{}")?)
                } else {
                    format!("Answer only with JSON shaped like this example, no other text:\n{}", self.ctx.s("jsonSchemaExample", i, "{}")?)
                }
            }
            "outputParserItemList" => {
                let n = self.ctx.n("options.numberOfItems", i, 0.0)?;
                let sep = self.ctx.s("options.separator", i, "\\n")?;
                format!("Answer with {}a list, one item per line{}, without numbering or extra text.", if n > 0.0 { format!("{n} items as ") } else { String::new() },
                        if sep == "\\n" { String::new() } else { format!(" separated by \"{sep}\"") })
            }
            "outputParserAutofixing" => self.inner().map(|p| p.instructions(i)).transpose()?.unwrap_or_default(),
            _ => String::new(),
        })
    }

    pub async fn parse(&self, text: &str, i: usize) -> Result<Value> {
        match short(&self.node.kind) {
            "outputParserStructured" => parse_json(text),
            "outputParserItemList" => {
                let sep = self.ctx.s("options.separator", i, "\\n")?.replace("\\n", "\n");
                let n = self.ctx.n("options.numberOfItems", i, 0.0)? as usize;
                let mut items: Vec<Value> = text.split(sep.as_str()).map(|s| s.trim().trim_start_matches(['-', '*', ' ']).trim()).filter(|s| !s.is_empty()).map(|s| json!(s)).collect();
                if n > 0 {
                    items.truncate(n);
                }
                Ok(Value::Array(items))
            }
            "outputParserAutofixing" => {
                let inner = self.inner().ok_or_else(|| anyhow!("Connect an output parser to \"{}\"", self.node.name))?;
                match Box::pin(inner.parse(text, i)).await {
                    Ok(v) => Ok(v),
                    Err(e) => {
                        // Ask the fixing model once to repair the answer.
                        let m = require_model(&self.ctx, i)?;
                        let fixed = complete(&self.ctx, &m, "", &format!("{}\n\nThis answer could not be parsed ({e}):\n{text}\n\nReturn only the corrected answer.", inner.instructions(i)?)).await?;
                        Box::pin(inner.parse(&fixed, i)).await
                    }
                }
            }
            other => Err(anyhow!("'{other}' is not an output parser")),
        }
    }
}

// ---------------------------------------------------------------------------
// memory
// ---------------------------------------------------------------------------

pub struct Memory<'a> {
    node: &'a Node,
    ctx: Ctx<'a>,
    session: String,
    window: usize,
}

pub fn memory<'a>(ctx: &Ctx<'a>, i: usize) -> Result<Option<Memory<'a>>> {
    let Some(n) = subs(ctx, "ai_memory").first().copied() else { return Ok(None) };
    let sc = sub_ctx(ctx, n);
    let session = if sc.s("sessionIdType", i, "fromInput")? == "customKey" {
        sc.s("sessionKey", i, "")?
    } else {
        ctx.items().get(i).map(|it| as_str(&it.json["sessionId"])).unwrap_or_default()
    };
    if session.is_empty() {
        return Err(anyhow!("\"{}\" needs a session id: connect a Chat Trigger or set a custom key", n.name));
    }
    let window = sc.n("contextWindowLength", i, 5.0)?.max(1.0) as usize;
    Ok(Some(Memory { node: n, ctx: sc, session, window }))
}

fn lc_msg(role: &str, content: &str) -> Value {
    json!({"type": if role == "user" { "human" } else { "ai" }, "data": {"content": content, "additional_kwargs": {}, "response_metadata": {}}})
}

fn from_lc(v: &Value) -> Value {
    json!({"role": if v["type"] == "human" { "user" } else { "assistant" }, "content": as_str(&v["data"]["content"])})
}

impl Memory<'_> {
    fn table(&self) -> Result<String> {
        let t = self.ctx.s("tableName", 0, "n8n_chat_histories")?;
        if !t.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
            return Err(anyhow!("Invalid table name '{t}'"));
        }
        Ok(t)
    }

    async fn redis(&self) -> Result<redis::aio::MultiplexedConnection> {
        let c = self.ctx.creds("redis")?;
        let pw = as_str(&c["password"]);
        let auth = if pw.is_empty() { String::new() } else { format!(":{pw}@") };
        let url = format!("redis://{auth}{}:{}/{}", as_str(&c["host"]), as_f64(&c["port"]).unwrap_or(6379.0), as_f64(&c["database"]).unwrap_or(0.0));
        Ok(redis::Client::open(url)?.get_multiplexed_async_connection().await?)
    }

    /// The last `window` exchanges as plain {role, content} messages.
    pub async fn load(&self) -> Result<Vec<Value>> {
        let keep = self.window * 2;
        Ok(match short(&self.node.kind) {
            "memoryPostgresChat" => {
                let c = super::integrations::pg_connect(&self.ctx.creds("postgres")?).await?;
                let t = self.table()?;
                c.batch_execute(&format!("CREATE TABLE IF NOT EXISTS {t} (id SERIAL PRIMARY KEY, session_id VARCHAR(255) NOT NULL, message JSONB NOT NULL)")).await?;
                let rows = c.query(&format!("SELECT message::text FROM {t} WHERE session_id = $1 ORDER BY id DESC LIMIT {keep}"), &[&self.session]).await?;
                rows.iter().rev().filter_map(|r| serde_json::from_str::<Value>(&r.get::<_, String>(0)).ok()).map(|v| from_lc(&v)).collect()
            }
            "memoryRedisChat" => {
                let mut con = self.redis().await?;
                let raw: Vec<String> = redis::cmd("LRANGE").arg(&self.session).arg(0).arg(keep as i64 - 1).query_async(&mut con).await?;
                raw.iter().rev().filter_map(|s| serde_json::from_str::<Value>(s).ok()).map(|v| from_lc(&v)).collect()
            }
            _ => {
                let key = format!("memory:{}:{}", self.node.name, self.session);
                let all = self.ctx.eng.static_data.borrow()["global"][&key].as_array().cloned().unwrap_or_default();
                all[all.len().saturating_sub(keep)..].to_vec()
            }
        })
    }

    pub async fn save(&self, user: &str, answer: &str) -> Result<()> {
        match short(&self.node.kind) {
            "memoryPostgresChat" => {
                let c = super::integrations::pg_connect(&self.ctx.creds("postgres")?).await?;
                let t = self.table()?;
                c.batch_execute(&format!("CREATE TABLE IF NOT EXISTS {t} (id SERIAL PRIMARY KEY, session_id VARCHAR(255) NOT NULL, message JSONB NOT NULL)")).await?;
                for m in [lc_msg("user", user), lc_msg("assistant", answer)] {
                    c.execute(&format!("INSERT INTO {t} (session_id, message) VALUES ($1, $2::text::jsonb)"), &[&self.session, &m.to_string()]).await?;
                }
            }
            "memoryRedisChat" => {
                let mut con = self.redis().await?;
                for m in [lc_msg("user", user), lc_msg("assistant", answer)] {
                    let _: i64 = redis::cmd("LPUSH").arg(&self.session).arg(m.to_string()).query_async(&mut con).await?;
                }
                let ttl = self.ctx.n("sessionTTL", 0, 0.0)? as i64;
                if ttl > 0 {
                    let _: i64 = redis::cmd("EXPIRE").arg(&self.session).arg(ttl).query_async(&mut con).await?;
                }
            }
            _ => {
                let key = format!("memory:{}:{}", self.node.name, self.session);
                let mut sd = self.ctx.eng.static_data.borrow_mut();
                if !sd["global"].is_object() {
                    sd["global"] = json!({});
                }
                let mut all = sd["global"][&key].as_array().cloned().unwrap_or_default();
                all.push(json!({"role": "user", "content": user}));
                all.push(json!({"role": "assistant", "content": answer}));
                let keep = all.len().saturating_sub(self.window * 2);
                sd["global"][&key] = Value::Array(all[keep..].to_vec());
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// embeddings, documents, vector stores
// ---------------------------------------------------------------------------

/// (credential-like {apiKey, url}, model) for the OpenAI-compatible /embeddings endpoint.
pub fn embedder(ctx: &Ctx, i: usize) -> Result<(Value, String)> {
    let n = *subs(ctx, "ai_embedding").first().ok_or_else(|| anyhow!("Connect an embeddings model to \"{}\"", ctx.node.name))?;
    let sc = sub_ctx(ctx, n);
    let k = short(&n.kind);
    let (url, key, model) = match k {
        "embeddingsOpenAi" => {
            let c = sc.creds("openAiApi")?;
            (cred_url(&c, "url", "https://api.openai.com/v1"), as_str(&c["apiKey"]), rl(&sc, "model", i, "text-embedding-3-small")?)
        }
        "embeddingsOllama" => {
            let c = sc.creds("ollamaApi")?;
            (format!("{}/v1", cred_url(&c, "baseUrl", "http://localhost:11434")), String::new(), rl(&sc, "model", i, "nomic-embed-text")?)
        }
        "embeddingsMistralCloud" => {
            let c = sc.creds("mistralCloudApi")?;
            ("https://api.mistral.ai/v1".into(), as_str(&c["apiKey"]), rl(&sc, "model", i, "mistral-embed")?)
        }
        "embeddingsGoogleGemini" => {
            let c = sc.creds("googlePalmApi")?;
            (format!("{}/v1beta/openai", cred_url(&c, "host", "https://generativelanguage.googleapis.com")), as_str(&c["apiKey"]),
             rl(&sc, "modelName", i, "models/text-embedding-004")?.trim_start_matches("models/").to_string())
        }
        other => return Err(anyhow!("'{other}' is not an embeddings model")),
    };
    Ok((json!({"url": super::declarative::rebase(url), "apiKey": key}), model))
}

pub async fn embed(ctx: &Ctx<'_>, i: usize, texts: &[String]) -> Result<Vec<Vec<f32>>> {
    let (c, model) = embedder(ctx, i)?;
    super::vector::embed(&ctx.eng.app, &c, &model, texts).await
}

/// Splits text with the connected text splitter (or the default loader settings).
fn split(ctx: &Ctx, text: &str, i: usize) -> Result<Vec<String>> {
    let Some(n) = subs(ctx, "ai_textSplitter").first().copied() else { return Ok(super::vector::chunk(text, 1000, 200)) };
    let sc = sub_ctx(ctx, n);
    let size = sc.n("chunkSize", i, 1000.0)?.max(1.0) as usize;
    let overlap = sc.n("chunkOverlap", i, 0.0)? as usize;
    Ok(match short(&n.kind) {
        "textSplitterCharacterTextSplitter" => {
            let sep = sc.s("separator", i, "\n\n")?.replace("\\n", "\n");
            let mut out: Vec<String> = vec![];
            let mut cur = String::new();
            for part in text.split(sep.as_str()).filter(|p| !p.trim().is_empty()) {
                if !cur.is_empty() && cur.len() + sep.len() + part.len() > size {
                    out.push(std::mem::take(&mut cur));
                }
                if !cur.is_empty() {
                    cur.push_str(&sep);
                }
                cur.push_str(part);
            }
            if !cur.is_empty() {
                out.push(cur);
            }
            out
        }
        // Tokens are approximated as four characters.
        "textSplitterTokenSplitter" => super::vector::chunk(text, size * 4, overlap * 4),
        _ => super::vector::chunk(text, size, overlap),
    })
}

/// Text of a binary input (text, CSV, JSON or PDF), chosen by the loader setting or the file type.
fn binary_text(sc: &Ctx, it: &Item, i: usize) -> Result<String> {
    let key = sc.s("binaryDataKey", i, "data")?;
    let bin = it.binary.as_ref().and_then(|b| b.get(&key)).ok_or_else(|| anyhow!("No binary property '{key}' in the input"))?;
    let bytes = super::binary_bytes(bin)?;
    let mime = as_str(&bin["mimeType"]);
    let name = as_str(&bin["fileName"]).to_lowercase();
    let loader = sc.s("loader", i, "auto")?;
    let kind = if loader != "auto" {
        loader
    } else if mime.contains("pdf") || name.ends_with(".pdf") {
        "pdfLoader".into()
    } else if mime.contains("csv") || name.ends_with(".csv") {
        "csvLoader".into()
    } else if mime.contains("json") || name.ends_with(".json") {
        "jsonLoader".into()
    } else {
        "textLoader".into()
    };
    match kind.as_str() {
        "pdfLoader" => {
            // pdf-extract panics on some broken files; that must not take the execution down.
            let r = std::panic::catch_unwind(|| pdf_extract::extract_text_from_mem(&bytes));
            match r {
                Ok(Ok(t)) => Ok(t),
                Ok(Err(e)) => Err(anyhow!("Cannot read the PDF: {e}")),
                Err(_) => Err(anyhow!("Cannot read the PDF: the file is damaged or unsupported")),
            }
        }
        "csvLoader" => {
            let mut rdr = csv::Reader::from_reader(&bytes[..]);
            let head = rdr.headers()?.clone();
            let mut lines = vec![];
            for row in rdr.records() {
                let row = row?;
                lines.push(head.iter().zip(row.iter()).map(|(h, v)| format!("{h}: {v}")).collect::<Vec<_>>().join("\n"));
            }
            Ok(lines.join("\n\n"))
        }
        "jsonLoader" => Ok(serde_json::from_slice::<Value>(&bytes).map(|v| serde_json::to_string_pretty(&v).unwrap_or_default()).unwrap_or_else(|_| String::from_utf8_lossy(&bytes).into_owned())),
        _ => Ok(String::from_utf8_lossy(&bytes).into_owned()),
    }
}

/// Documents (text, metadata) for the input items through the connected document loader.
fn documents(ctx: &Ctx, items: &[Item]) -> Result<Vec<(String, Value)>> {
    let loader = subs(ctx, "ai_document").first().copied();
    let mut out = vec![];
    for (i, it) in items.iter().enumerate() {
        let (text, meta, lc) = match loader {
            Some(n) => {
                let sc = sub_ctx(ctx, n);
                let text = if sc.s("dataType", i, "json")? == "binary" {
                    binary_text(&sc, it, i)?
                } else if sc.s("jsonMode", i, "allInputData")? == "expressionData" {
                    sc.s("jsonData", i, "")?
                } else {
                    it.json.to_string()
                };
                let mut meta = serde_json::Map::new();
                for m in sc.list("options.metadata.metadataValues", i)? {
                    meta.insert(as_str(&m["name"]), m["value"].clone());
                }
                (text, Value::Object(meta), Some(sc))
            }
            None => (it.json.to_string(), json!({}), None),
        };
        let chunks = match &lc {
            Some(sc) if sc.s("textSplittingMode", i, "simple")? == "custom" => split(sc, &text, i)?,
            _ => super::vector::chunk(&text, 1000, 200),
        };
        out.extend(chunks.into_iter().map(|c| (c, meta.clone())));
    }
    Ok(out)
}

/// Where a vector store node keeps its vectors.
enum Store {
    /// Flowwolf's own table (In-Memory Vector Store in n8n terms, but persistent).
    Local(String),
    Qdrant { url: String, key: String, collection: String },
    Pinecone { key: String, index: String, namespace: String },
    /// Supabase (pgvector behind PostgREST) with a `match_documents`-style function.
    Supabase { url: String, key: String, table: String, query: String },
    PgVector { creds: Value, table: String },
}

fn ident(t: &str) -> Result<String> {
    if t.is_empty() || !t.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(anyhow!("Invalid table name '{t}'"));
    }
    Ok(t.to_string())
}

fn vec_lit(e: &[f32]) -> String {
    format!("[{}]", e.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(","))
}

async fn pg_vectors(creds: &Value, table: &str) -> Result<tokio_postgres::Client> {
    let c = super::integrations::pg_connect(creds).await?;
    c.batch_execute(&format!(
        "CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE IF NOT EXISTS {table} (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), text text, metadata jsonb, embedding vector)"
    ))
    .await
    .map_err(|e| anyhow!("PGVector setup failed (is the pgvector extension installed?): {e}"))?;
    Ok(c)
}

/// Pinecone answers on a per-index host that the control plane names.
async fn pinecone_base(ctx: &Ctx<'_>, key: &str, index: &str) -> Result<String> {
    let url = format!("{}/indexes/{}", super::declarative::rebase("https://api.pinecone.io".into()), urlenc(index));
    let r = ctx.eng.app.http.get(url).header("Api-Key", key).header("X-Pinecone-API-Version", "2025-01").timeout(Duration::from_secs(30)).send().await?;
    let status = r.status();
    let v: Value = r.json().await.unwrap_or(Value::Null);
    let host = as_str(&v["host"]);
    if !status.is_success() || host.is_empty() {
        return Err(anyhow!("Pinecone index '{index}' not found ({status})"));
    }
    Ok(super::declarative::rebase(format!("https://{host}")))
}

async fn pinecone(ctx: &Ctx<'_>, base: &str, key: &str, path: &str, body: Value) -> Result<Value> {
    let r = ctx.eng.app.http.post(format!("{base}{path}")).header("Api-Key", key).header("X-Pinecone-API-Version", "2025-01").json(&body).timeout(Duration::from_secs(60)).send().await?;
    let status = r.status();
    let v: Value = r.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        return Err(anyhow!("Pinecone returned {status}: {}", as_str(&v["message"])));
    }
    Ok(v)
}

async fn supabase(ctx: &Ctx<'_>, url: &str, key: &str, method: reqwest::Method, path: &str, body: Value) -> Result<Value> {
    let mut rb = ctx.eng.app.http.request(method, format!("{url}{path}")).header("apikey", key).bearer_auth(key).header("Prefer", "return=minimal").timeout(Duration::from_secs(60));
    if !body.is_null() {
        rb = rb.json(&body);
    }
    let r = rb.send().await?;
    let status = r.status();
    let v: Value = r.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        return Err(anyhow!("Supabase returned {status}: {}", as_str(&v["message"])));
    }
    Ok(v)
}

fn store_of(sc: &Ctx, i: usize) -> Result<Store> {
    Ok(match short(&sc.node.kind) {
        "vectorStoreQdrant" => {
            let c = sc.creds("qdrantApi")?;
            Store::Qdrant { url: super::declarative::rebase(cred_url(&c, "qdrantUrl", "http://localhost:6333")), key: as_str(&c["apiKey"]), collection: rl(sc, "qdrantCollection", i, "")? }
        }
        "vectorStorePinecone" => Store::Pinecone {
            key: as_str(&sc.creds("pineconeApi")?["apiKey"]),
            index: rl(sc, "pineconeIndex", i, "")?,
            namespace: sc.s("options.pineconeNamespace", i, "")?,
        },
        "vectorStoreSupabase" => {
            let c = sc.creds("supabaseApi")?;
            Store::Supabase {
                url: super::declarative::rebase(cred_url(&c, "host", "")),
                key: as_str(&c["serviceRole"]),
                table: ident(&rl(sc, "tableName", i, "documents")?)?,
                query: ident(&sc.s("options.queryName", i, "match_documents")?)?,
            }
        }
        "vectorStorePGVector" => Store::PgVector { creds: sc.creds("postgres")?, table: ident(&sc.s("tableName", i, "n8n_vectors")?)? },
        _ => Store::Local(rl(sc, "memoryKey", i, "vector_store_key")?),
    })
}

async fn qdrant(ctx: &Ctx<'_>, url: &str, key: &str, method: reqwest::Method, path: &str, body: Value) -> Result<Value> {
    let mut rb = ctx.eng.app.http.request(method, format!("{url}{path}")).json(&body).timeout(Duration::from_secs(60));
    if !key.is_empty() {
        rb = rb.header("api-key", key);
    }
    let r = rb.send().await?;
    let status = r.status();
    let v: Value = r.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        return Err(anyhow!("Qdrant returned {status}: {}", v["status"]["error"].as_str().unwrap_or("")));
    }
    Ok(v)
}

async fn store_insert(ctx: &Ctx<'_>, store: &Store, docs: &[(String, Value)], vecs: &[Vec<f32>], clear: bool) -> Result<()> {
    match store {
        Store::Local(k) => {
            if clear {
                // The table may not exist yet on a fresh instance.
                super::vector::ensure_table(&ctx.eng.app)?;
                ctx.eng.app.db.exec("DELETE FROM vectors WHERE store = ?1", crate::p![k.as_str()])?;
            }
            for ((t, m), e) in docs.iter().zip(vecs) {
                super::vector::insert(&ctx.eng.app, k, t, m, e)?;
            }
        }
        Store::Qdrant { url, key, collection } => {
            let path = format!("/collections/{}", urlenc(collection));
            let exists = qdrant(ctx, url, key, reqwest::Method::GET, &path, Value::Null).await.is_ok();
            if clear && exists {
                qdrant(ctx, url, key, reqwest::Method::DELETE, &path, Value::Null).await?;
            }
            if clear || !exists {
                let size = vecs.first().map(|v| v.len()).unwrap_or(0);
                qdrant(ctx, url, key, reqwest::Method::PUT, &path, json!({"vectors": {"size": size, "distance": "Cosine"}})).await?;
            }
            let points: Vec<Value> = docs.iter().zip(vecs).map(|((t, m), e)| json!({"id": uuid::Uuid::new_v4().to_string(), "vector": e, "payload": {"content": t, "metadata": m}})).collect();
            qdrant(ctx, url, key, reqwest::Method::PUT, &format!("{path}/points?wait=true"), json!({"points": points})).await?;
        }
        Store::Pinecone { key, index, namespace } => {
            let base = pinecone_base(ctx, key, index).await?;
            if clear {
                // A namespace that does not exist yet answers 404; that is fine here.
                let _ = pinecone(ctx, &base, key, "/vectors/delete", json!({"deleteAll": true, "namespace": namespace})).await;
            }
            let vectors: Vec<Value> = docs
                .iter()
                .zip(vecs)
                .map(|((t, m), e)| {
                    let mut md = if m.is_object() { m.clone() } else { json!({}) };
                    md["text"] = json!(t);
                    json!({"id": uuid::Uuid::new_v4().to_string(), "values": e, "metadata": md})
                })
                .collect();
            for batch in vectors.chunks(100) {
                pinecone(ctx, &base, key, "/vectors/upsert", json!({"vectors": batch, "namespace": namespace})).await?;
            }
        }
        Store::Supabase { url, key, table, .. } => {
            if clear {
                supabase(ctx, url, key, reqwest::Method::DELETE, &format!("/rest/v1/{table}?id=not.is.null"), Value::Null).await?;
            }
            let rows: Vec<Value> = docs.iter().zip(vecs).map(|((t, m), e)| json!({"content": t, "metadata": m, "embedding": e})).collect();
            supabase(ctx, url, key, reqwest::Method::POST, &format!("/rest/v1/{table}"), Value::Array(rows)).await?;
        }
        Store::PgVector { creds, table } => {
            let c = pg_vectors(creds, table).await?;
            if clear {
                c.execute(&format!("DELETE FROM {table}"), &[]).await?;
            }
            for ((t, m), e) in docs.iter().zip(vecs) {
                c.execute(&format!("INSERT INTO {table} (text, metadata, embedding) VALUES ($1, $2::text::jsonb, $3::text::vector)"), &[t, &m.to_string(), &vec_lit(e)]).await?;
            }
        }
    }
    Ok(())
}

/// Top-k documents as {pageContent, metadata, score}.
async fn store_search(ctx: &Ctx<'_>, store: &Store, q: &[f32], k: usize) -> Result<Vec<Value>> {
    Ok(match store {
        Store::Local(key) => super::vector::search(&ctx.eng.app, key, q, k)?
            .into_iter()
            .map(|h| json!({"pageContent": h["text"], "metadata": h["metadata"], "score": h["score"]}))
            .collect(),
        Store::Qdrant { url, key, collection } => {
            let v = qdrant(ctx, url, key, reqwest::Method::POST, &format!("/collections/{}/points/search", urlenc(collection)), json!({"vector": q, "limit": k, "with_payload": true})).await?;
            v["result"].as_array().into_iter().flatten().map(|h| json!({"pageContent": h["payload"]["content"], "metadata": h["payload"]["metadata"], "score": h["score"]})).collect()
        }
        Store::Pinecone { key, index, namespace } => {
            let base = pinecone_base(ctx, key, index).await?;
            let v = pinecone(ctx, &base, key, "/query", json!({"vector": q, "topK": k, "includeMetadata": true, "namespace": namespace})).await?;
            v["matches"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|h| {
                    let mut md = h["metadata"].clone();
                    let text = md.as_object_mut().and_then(|o| o.remove("text")).unwrap_or(Value::Null);
                    json!({"pageContent": text, "metadata": md, "score": h["score"]})
                })
                .collect()
        }
        Store::Supabase { url, key, query, .. } => {
            let v = supabase(ctx, url, key, reqwest::Method::POST, &format!("/rest/v1/rpc/{query}"), json!({"query_embedding": q, "match_count": k, "filter": {}})).await?;
            v.as_array().into_iter().flatten().map(|h| json!({"pageContent": h["content"], "metadata": h["metadata"], "score": h["similarity"]})).collect()
        }
        Store::PgVector { creds, table } => {
            let c = pg_vectors(creds, table).await?;
            let rows = c
                .query(&format!("SELECT text, metadata::text, (1 - (embedding <=> $1::text::vector))::float8 FROM {table} ORDER BY embedding <=> $1::text::vector LIMIT {k}"), &[&vec_lit(q)])
                .await?;
            rows.iter()
                .map(|r| json!({"pageContent": r.get::<_, Option<String>>(0), "metadata": serde_json::from_str::<Value>(&r.get::<_, Option<String>>(1).unwrap_or_default()).unwrap_or(json!({})), "score": r.get::<_, f64>(2)}))
                .collect()
        }
    })
}

/// Retrieval through a vector store sub-node (which has its own embeddings sub-node).
async fn retrieve(store_ctx: &Ctx<'_>, query: &str, k: usize, i: usize) -> Result<Vec<Value>> {
    let q = embed(store_ctx, i, &[query.to_string()]).await?.remove(0);
    store_search(store_ctx, &store_of(store_ctx, i)?, &q, k).await
}

async fn vector_store(ctx: &Ctx<'_>) -> Result<Out> {
    let mode = ctx.s("mode", 0, "retrieve")?;
    let store = store_of(ctx, 0)?;
    match mode.as_str() {
        "insert" => {
            let docs = documents(ctx, ctx.items())?;
            let texts: Vec<String> = docs.iter().map(|d| d.0.clone()).collect();
            let vecs = embed(ctx, 0, &texts).await?;
            let clear = ctx.b("clearStore", 0, false)? || ctx.b("options.clearStore", 0, false)? || ctx.b("options.clearNamespace", 0, false)?;
            store_insert(ctx, &store, &docs, &vecs, clear).await?;
            Ok(vec![ctx.items().iter().enumerate().map(|(i, it)| Item::paired(it.json.clone(), i)).collect()])
        }
        "load" => {
            let mut out = vec![];
            for i in 0..ctx.items().len() {
                let prompt = ctx.s("prompt", i, "")?;
                let k = ctx.n("topK", i, 4.0)?.max(1.0) as usize;
                let q = embed(ctx, i, &[prompt]).await?.remove(0);
                for d in store_search(ctx, &store, &q, k).await? {
                    out.push(Item::paired(json!({"document": {"pageContent": d["pageContent"], "metadata": d["metadata"]}, "score": d["score"]}), i));
                }
            }
            Ok(vec![out])
        }
        other => Err(anyhow!("Mode '{other}' of \"{}\" only works as a sub-node (connect it to an agent or retriever)", ctx.node.name)),
    }
}

// ---------------------------------------------------------------------------
// tools
// ---------------------------------------------------------------------------

fn tool_name(s: &str) -> String {
    let n: String = s.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' }).collect();
    n.chars().take(64).collect()
}

/// MCP over streamable HTTP: JSON-RPC posts; answers may come as JSON or as a server-sent event.
async fn mcp_rpc(app: &crate::app::AppState, endpoint: &str, headers: &[(String, String)], session: &mut Option<String>, method: &str, params: Value, id: Option<i64>) -> Result<Value> {
    let mut body = json!({"jsonrpc": "2.0", "method": method, "params": params});
    if let Some(id) = id {
        body["id"] = json!(id);
    }
    let mut rb = app.http.post(super::declarative::rebase(endpoint.to_string())).header("accept", "application/json, text/event-stream").json(&body).timeout(Duration::from_secs(60));
    for (k, v) in headers {
        rb = rb.header(k, v);
    }
    if let Some(s) = session.as_ref() {
        rb = rb.header("mcp-session-id", s);
    }
    let r = rb.send().await.map_err(|e| anyhow!("MCP server not reachable: {e}"))?;
    if let Some(s) = r.headers().get("mcp-session-id").and_then(|v| v.to_str().ok()) {
        *session = Some(s.to_string());
    }
    let status = r.status();
    let text = r.text().await.unwrap_or_default();
    if id.is_none() {
        return Ok(Value::Null);
    }
    if !status.is_success() {
        return Err(anyhow!("MCP server returned {status}: {}", text.chars().take(200).collect::<String>()));
    }
    let json_text = if text.trim_start().starts_with('{') { text.clone() } else { text.lines().filter_map(|l| l.strip_prefix("data:")).last().unwrap_or("").trim().to_string() };
    let v: Value = serde_json::from_str(&json_text).map_err(|_| anyhow!("Unexpected MCP answer: {}", text.chars().take(200).collect::<String>()))?;
    if !v["error"].is_null() {
        return Err(anyhow!("MCP error: {}", as_str(&v["error"]["message"])));
    }
    Ok(v["result"].clone())
}

/// An open MCP connection: streamable HTTP (session header) or the older HTTP+SSE transport,
/// where answers arrive as `message` events on a GET stream and requests go to the URL the
/// server announced in its `endpoint` event.
enum McpConn {
    Http { endpoint: String, session: Option<String> },
    Sse { post: String, rx: tokio::sync::mpsc::UnboundedReceiver<Value>, _reader: AbortOnDrop },
}

struct AbortOnDrop(tokio::task::JoinHandle<()>);
impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// Splits a server-sent event stream into (event name, data) pairs.
fn sse_events(buf: &mut String) -> Vec<(String, String)> {
    let mut out = vec![];
    *buf = buf.replace("\r\n", "\n");
    while let Some(p) = buf.find("\n\n") {
        let block: String = buf.drain(..p + 2).collect();
        let (mut event, mut data) = ("message".to_string(), vec![]);
        for l in block.lines() {
            if let Some(v) = l.strip_prefix("event:") {
                event = v.trim().to_string();
            } else if let Some(v) = l.strip_prefix("data:") {
                data.push(v.strip_prefix(' ').unwrap_or(v).to_string());
            }
        }
        if !data.is_empty() {
            out.push((event, data.join("\n")));
        }
    }
    out
}

async fn mcp_connect(app: &crate::app::AppState, endpoint: &str, headers: &[(String, String)], sse: bool) -> Result<McpConn> {
    let mut conn = if sse {
        use futures::StreamExt;
        let mut rb = app.http.get(super::declarative::rebase(endpoint.to_string())).header("accept", "text/event-stream");
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let r = rb.send().await.map_err(|e| anyhow!("MCP server not reachable: {e}"))?;
        if !r.status().is_success() {
            return Err(anyhow!("MCP server returned {} for the SSE stream", r.status()));
        }
        let (etx, erx) = tokio::sync::oneshot::channel::<String>();
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        let reader = tokio::spawn(async move {
            let (mut etx, mut buf, mut body) = (Some(etx), String::new(), r.bytes_stream());
            while let Some(Ok(chunk)) = body.next().await {
                buf.push_str(&String::from_utf8_lossy(&chunk));
                for (event, data) in sse_events(&mut buf) {
                    if event == "endpoint" {
                        if let Some(t) = etx.take() {
                            let _ = t.send(data);
                        }
                    } else if let Ok(v) = serde_json::from_str::<Value>(&data) {
                        let _ = tx.send(v);
                    }
                }
            }
        });
        let reader = AbortOnDrop(reader);
        let announced = tokio::time::timeout(Duration::from_secs(30), erx).await.map_err(|_| anyhow!("MCP server sent no endpoint event"))?
            .map_err(|_| anyhow!("MCP SSE stream closed before the endpoint event"))?;
        let post = reqwest::Url::parse(endpoint)?.join(announced.trim())?.to_string();
        McpConn::Sse { post, rx, _reader: reader }
    } else {
        McpConn::Http { endpoint: endpoint.to_string(), session: None }
    };
    mcp_call(app, &mut conn, headers, "initialize",
             json!({"protocolVersion": if sse { "2024-11-05" } else { "2025-03-26" }, "capabilities": {}, "clientInfo": {"name": "flowwolf", "version": env!("CARGO_PKG_VERSION")}}), Some(1)).await?;
    mcp_call(app, &mut conn, headers, "notifications/initialized", json!({}), None).await?;
    Ok(conn)
}

async fn mcp_call(app: &crate::app::AppState, conn: &mut McpConn, headers: &[(String, String)], method: &str, params: Value, id: Option<i64>) -> Result<Value> {
    let (post, rx) = match conn {
        McpConn::Http { endpoint, session } => return mcp_rpc(app, endpoint, headers, session, method, params, id).await,
        McpConn::Sse { post, rx, .. } => (post.clone(), rx),
    };
    let mut body = json!({"jsonrpc": "2.0", "method": method, "params": params});
    if let Some(id) = id {
        body["id"] = json!(id);
    }
    let mut rb = app.http.post(super::declarative::rebase(post)).json(&body).timeout(Duration::from_secs(60));
    for (k, v) in headers {
        rb = rb.header(k, v);
    }
    let r = rb.send().await.map_err(|e| anyhow!("MCP server not reachable: {e}"))?;
    if !r.status().is_success() {
        return Err(anyhow!("MCP server returned {}", r.status()));
    }
    let Some(id) = id else { return Ok(Value::Null) };
    let v = tokio::time::timeout(Duration::from_secs(60), async {
        while let Some(v) = rx.recv().await {
            if v["id"] == json!(id) {
                return Some(v);
            }
        }
        None
    }).await.map_err(|_| anyhow!("MCP server did not answer {method}"))?.ok_or_else(|| anyhow!("MCP SSE stream closed"))?;
    if !v["error"].is_null() {
        return Err(anyhow!("MCP error: {}", as_str(&v["error"]["message"])));
    }
    Ok(v["result"].clone())
}

fn mcp_headers(sc: &Ctx, i: usize) -> Result<Vec<(String, String)>> {
    Ok(match sc.s("authentication", i, "none")?.as_str() {
        "bearerAuth" => vec![("authorization".into(), format!("Bearer {}", as_str(&sc.creds("httpBearerAuth")?["token"])))],
        "headerAuth" => {
            let c = sc.creds("httpHeaderAuth")?;
            vec![(as_str(&c["name"]), as_str(&c["value"]))]
        }
        _ => vec![],
    })
}

/// All tools for a root node: its tool sub-nodes (an MCP client adds one tool per server tool).
pub async fn tools(ctx: &Ctx<'_>, i: usize) -> Result<Vec<Tool>> {
    let mut out = vec![];
    for n in subs(ctx, "ai_tool") {
        let sc = sub_ctx(ctx, n);
        let k = short(&n.kind);
        let desc = |d: &str| -> Result<String> {
            let v = sc.s("toolDescription", i, "")?;
            Ok(if v.is_empty() { sc.s("description", i, d)? } else { v })
        };
        let named = |d: &str| -> Result<String> {
            let v = sc.s("name", i, "")?;
            Ok(tool_name(if v.is_empty() { d } else { &v }))
        };
        match k {
            "mcpClientTool" => {
                let endpoint = sc.s("endpointUrl", i, "")?;
                // Version 1 only had `sseEndpoint`; later versions pick the transport.
                let (endpoint, sse) = if endpoint.is_empty() { (sc.s("sseEndpoint", i, "")?, true) } else { (endpoint, sc.s("serverTransport", i, "httpStreamable")? == "sse") };
                let headers = mcp_headers(&sc, i)?;
                let mut conn = mcp_connect(&ctx.eng.app, &endpoint, &headers, sse).await?;
                let listed = mcp_call(&ctx.eng.app, &mut conn, &headers, "tools/list", json!({}), Some(2)).await?;
                let pick = sc.s("include", i, "all")?;
                let chosen: Vec<String> = sc.val("includeTools", i, json!([]))?.as_array().into_iter().flatten().map(as_str).collect();
                let excluded: Vec<String> = sc.val("excludeTools", i, json!([]))?.as_array().into_iter().flatten().map(as_str).collect();
                for t in listed["tools"].as_array().into_iter().flatten() {
                    let name = as_str(&t["name"]);
                    if (pick == "selected" && !chosen.contains(&name)) || (pick == "except" && excluded.contains(&name)) {
                        continue;
                    }
                    out.push(Tool {
                        name: tool_name(&name),
                        description: as_str(&t["description"]),
                        schema: if t["inputSchema"].is_object() { t["inputSchema"].clone() } else { json!({"type": "object", "properties": {}}) },
                        run: ToolRun::Mcp { endpoint: endpoint.clone(), headers: headers.clone(), sse, tool: name },
                    });
                }
            }
            _ => {
                let (name, description) = match k {
                    "toolCalculator" => ("calculator".to_string(), "Useful for getting the result of a math expression. The input must be a valid mathematical expression.".to_string()),
                    "toolThink" => ("think".to_string(), desc("Use the tool to think about something. It will not obtain new information, it only notes the thought.")?),
                    "toolWikipedia" => ("wikipedia_api".to_string(), "Searches Wikipedia and returns the summary of the best matching article. Input: a search query.".to_string()),
                    "toolSerpApi" => ("serpapi".to_string(), "Searches the web with Google through SerpApi. Input: a search query.".to_string()),
                    "toolWolframAlpha" => ("wolfram_alpha".to_string(), "Answers questions about math, science, dates and units with Wolfram|Alpha. Input: a question.".to_string()),
                    "toolSearXng" => ("searxng".to_string(), "Searches the web with a SearXNG instance. Input: a search query.".to_string()),
                    "toolCode" => (named("code_tool")?, desc("Runs code")?),
                    "toolHttpRequest" => (named(&n.name)?, desc("Calls an HTTP endpoint")?),
                    "toolWorkflow" => (named(&n.name)?, desc("Runs a workflow")?),
                    "toolVectorStore" => (named(&n.name)?, desc("Answers questions from a knowledge base")?),
                    k if is_vector_store(k) => (tool_name(&{
                        let v = sc.s("toolName", i, "")?;
                        if v.is_empty() { n.name.clone() } else { v }
                    }), desc("Retrieves documents from a vector store")?),
                    other => return Err(anyhow!("'{other}' cannot be used as a tool")),
                };
                out.push(Tool { name, description, schema: input_schema(), run: ToolRun::Node(n.clone()) });
            }
        }
    }
    Ok(out)
}

fn arg_text(args: &Value) -> String {
    match args.get("input").or_else(|| args.get("query")) {
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
        None if args.is_string() => as_str(args),
        None => args.to_string(),
    }
}

/// Math only: digits, operators, parentheses and names of Math functions/constants.
fn calculate(ctx: &Ctx, expr: &str) -> Result<String> {
    const OK: &[&str] = &["abs", "acos", "asin", "atan", "atan2", "ceil", "cos", "exp", "floor", "log", "log10", "log2", "max", "min", "pow", "round", "sign", "sin", "sqrt", "tan", "PI", "E"];
    let words = expr.split(|c: char| !c.is_ascii_alphabetic() && c != '_').filter(|w| !w.is_empty());
    for w in words {
        if !OK.contains(&w) {
            return Err(anyhow!("'{w}' is not allowed in a math expression"));
        }
    }
    if expr.chars().any(|c| !(c.is_ascii_alphanumeric() || " .,+-*/%()^_".contains(c))) {
        return Err(anyhow!("Only numbers, operators and Math functions are allowed"));
    }
    let js = expr.replace('^', "**");
    let code = format!("const {{ {} }} = Math; return [{{json: {{r: ({js})}}}}];", OK.join(", "));
    let v = ctx.eng.js.run_code(&code, false, 5000)?;
    Ok(v[0]["json"]["r"].to_string())
}

pub async fn call_tool(ctx: &Ctx<'_>, tool: &Tool, args: &Value, i: usize) -> String {
    let res: Result<String> = async {
        let input = arg_text(args);
        match &tool.run {
            ToolRun::Legacy(_) => Err(anyhow!("legacy tools are handled by the agent")),
            ToolRun::Mcp { endpoint, headers, sse, tool } => {
                let mut conn = mcp_connect(&ctx.eng.app, endpoint, headers, *sse).await?;
                let r = mcp_call(&ctx.eng.app, &mut conn, headers, "tools/call", json!({"name": tool, "arguments": args}), Some(3)).await?;
                Ok(r["content"].as_array().into_iter().flatten().map(|c| c["text"].as_str().map(String::from).unwrap_or_else(|| c.to_string())).collect::<Vec<_>>().join("\n"))
            }
            ToolRun::Node(n) => {
                let sc = sub_ctx(ctx, n);
                let http = &ctx.eng.app.http;
                match short(&n.kind) {
                    "toolCalculator" => calculate(ctx, &input),
                    "toolThink" => Ok(input),
                    "toolCode" => {
                        let code = sc.s("jsCode", i, "return query;")?;
                        let wrapped = format!("const query = {}; const __r = (() => {{\n{code}\n}})(); return [{{json: {{r: __r}}}}];", json!(input));
                        let v = ctx.eng.js.run_code(&wrapped, false, ctx.eng.app.cfg.code_timeout_ms)?;
                        Ok(match &v[0]["json"]["r"] {
                            Value::String(s) => s.clone(),
                            other => other.to_string(),
                        })
                    }
                    "toolHttpRequest" => {
                        let enc = urlenc(&input);
                        let url = sc.s("url", i, "")?.replace("{input}", &enc).replace("{query}", &enc);
                        let method = sc.s("method", i, "GET")?.to_uppercase();
                        let mut rb = http.request(reqwest::Method::from_bytes(method.as_bytes())?, url).timeout(Duration::from_secs(60));
                        if method != "GET" {
                            rb = rb.json(&json!({"input": input}));
                        }
                        let r = rb.send().await?;
                        let status = r.status();
                        let t = r.text().await?;
                        Ok(if status.is_success() { t } else { format!("HTTP {status}: {t}") })
                    }
                    "toolWorkflow" => {
                        let id = rl(&sc, "workflowId", i, "")?;
                        let wf = ctx.eng.app.db.workflow(&id)?.ok_or_else(|| anyhow!("Tool workflow {id} not found"))?;
                        let out = crate::engine::run_sub(ctx.eng, wf, vec![Item::new(json!({"query": input}))]).await?;
                        Ok(serde_json::to_string(&out.into_iter().map(|i| i.json).collect::<Vec<_>>())?)
                    }
                    "toolWikipedia" => {
                        let base = super::declarative::rebase("https://en.wikipedia.org".into());
                        let s: Value = http.get(format!("{base}/w/api.php")).query(&[("action", "query"), ("list", "search"), ("srsearch", input.as_str()), ("format", "json"), ("srlimit", "1")])
                            .header("user-agent", "flowwolf").send().await?.json().await?;
                        let title = as_str(&s["query"]["search"][0]["title"]);
                        if title.is_empty() {
                            return Ok("No good Wikipedia search result was found".into());
                        }
                        let p: Value = http.get(format!("{base}/api/rest_v1/page/summary/{}", urlenc(&title))).header("user-agent", "flowwolf").send().await?.json().await?;
                        Ok(format!("Page: {title}\nSummary: {}", as_str(&p["extract"])))
                    }
                    "toolSerpApi" => {
                        let c = sc.creds("serpApi")?;
                        let v: Value = http.get(super::declarative::rebase("https://serpapi.com/search.json".into())).query(&[("q", input.as_str()), ("api_key", as_str(&c["apiKey"]).as_str())]).send().await?.json().await?;
                        let answer = as_str(&v["answer_box"]["answer"]);
                        if !answer.is_empty() {
                            return Ok(answer);
                        }
                        Ok(v["organic_results"].as_array().into_iter().flatten().take(5).map(|r| format!("{}: {}", as_str(&r["title"]), as_str(&r["snippet"]))).collect::<Vec<_>>().join("\n"))
                    }
                    "toolWolframAlpha" => {
                        let c = sc.creds("wolframAlphaApi")?;
                        let r = http.get(super::declarative::rebase("https://api.wolframalpha.com/v1/result".into())).query(&[("i", input.as_str()), ("appid", as_str(&c["appId"]).as_str())]).send().await?;
                        Ok(r.text().await?)
                    }
                    "toolSearXng" => {
                        let c = sc.creds("searXngApi")?;
                        let v: Value = http.get(format!("{}/search", cred_url(&c, "apiUrl", ""))).query(&[("q", input.as_str()), ("format", "json")]).send().await?.json().await?;
                        Ok(v["results"].as_array().into_iter().flatten().take(5).map(|r| format!("{} ({}): {}", as_str(&r["title"]), as_str(&r["url"]), as_str(&r["content"]))).collect::<Vec<_>>().join("\n"))
                    }
                    "toolVectorStore" => {
                        let store = *subs(&sc, "ai_vectorStore").first().ok_or_else(|| anyhow!("Connect a vector store to \"{}\"", n.name))?;
                        let k = sc.n("topK", i, 4.0)?.max(1.0) as usize;
                        let docs = retrieve(&sub_ctx(&sc, store), &input, k, i).await?;
                        Ok(docs.iter().map(|d| as_str(&d["pageContent"])).collect::<Vec<_>>().join("\n---\n"))
                    }
                    k if is_vector_store(k) => {
                        let k = sc.n("topK", i, 4.0)?.max(1.0) as usize;
                        let docs = retrieve(&sc, &input, k, i).await?;
                        Ok(docs.iter().map(|d| as_str(&d["pageContent"])).collect::<Vec<_>>().join("\n---\n"))
                    }
                    other => Err(anyhow!("'{other}' cannot run as a tool")),
                }
            }
        }
    }
    .await;
    // Tool errors go back to the model so it can recover.
    res.unwrap_or_else(|e| format!("Error: {e}")).chars().take(20_000).collect()
}

// ---------------------------------------------------------------------------
// root nodes: chains
// ---------------------------------------------------------------------------

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    let k = kind.strip_prefix(LC)?;
    Some(match k {
        "chainLlm" => chain_llm(ctx).await,
        "sentimentAnalysis" => sentiment(ctx).await,
        "textClassifier" => classifier(ctx).await,
        "informationExtractor" => extractor(ctx).await,
        "chainSummarization" => summarize(ctx).await,
        "chainRetrievalQa" => retrieval_qa(ctx).await,
        k if is_vector_store(k) => vector_store(ctx).await,
        "mcpTrigger" => Ok(vec![ctx.input(0)]),
        // Sub-nodes have no main input and only run through their root.
        _ if is_sub_node(kind) => Err(anyhow!("\"{}\" is a sub-node: connect it to an AI Agent, a chain or a vector store", ctx.node.name)),
        _ => return None,
    })
}

fn is_vector_store(k: &str) -> bool {
    matches!(k, "vectorStoreInMemory" | "vectorStoreQdrant" | "vectorStorePinecone" | "vectorStoreSupabase" | "vectorStorePGVector")
}

pub fn is_sub_node(kind: &str) -> bool {
    let k = short(kind);
    k.starts_with("lmChat") || k.starts_with("lm") && k.len() > 2 && k.as_bytes()[2].is_ascii_uppercase() || k.starts_with("memory") || k.starts_with("tool")
        || k.starts_with("embeddings") || k.starts_with("outputParser") || k.starts_with("textSplitter") || k.starts_with("document") || k.starts_with("retriever")
        || k == "mcpClientTool"
}

fn prompt(ctx: &Ctx, i: usize, field: &str) -> Result<String> {
    if ctx.s("promptType", i, "auto")? == "define" {
        ctx.s(field, i, "")
    } else {
        ctx.items().get(i).and_then(|it| it.json.get("chatInput")).map(as_str).ok_or_else(|| anyhow!("No 'chatInput' field in the input. Set 'Source for Prompt' to 'Define below'"))
    }
}

async fn chain_llm(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let m = require_model(ctx, i)?;
        let mut system = vec![];
        for msg in ctx.list("messages.messageValues", i)? {
            if msg["type"].as_str().unwrap_or("SystemMessagePromptTemplate") == "SystemMessagePromptTemplate" {
                system.push(as_str(&msg["message"]));
            }
        }
        let mut p = prompt(ctx, i, "text")?;
        let parser = parser(ctx);
        if let Some(pa) = &parser {
            p = format!("{p}\n\n{}", pa.instructions(i)?);
        }
        let text = complete(ctx, &m, &system.join("\n"), &p).await?;
        let j = match &parser {
            Some(pa) => json!({"output": pa.parse(&text, i).await?}),
            None => json!({"text": text}),
        };
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

fn categories(ctx: &Ctx, i: usize) -> Result<Vec<String>> {
    Ok(ctx.s("options.categories", i, "Positive, Neutral, Negative")?.split(',').map(|c| c.trim().to_string()).filter(|c| !c.is_empty()).collect())
}

/// Branch count of the classifier-style nodes (one output per category).
pub fn output_count(node: &Node) -> Option<usize> {
    match short(&node.kind) {
        "sentimentAnalysis" => {
            let c = node.parameters["options"]["categories"].as_str().unwrap_or("Positive, Neutral, Negative");
            Some(c.split(',').filter(|c| !c.trim().is_empty()).count().max(1))
        }
        "textClassifier" => {
            let n = node.parameters["categories"]["categories"].as_array().map(|a| a.len()).unwrap_or(0);
            let other = node.parameters["options"]["fallback"] == "other";
            Some(n.max(1) + other as usize)
        }
        _ => None,
    }
}

async fn sentiment(ctx: &Ctx<'_>) -> Result<Out> {
    let cats = categories(ctx, 0)?;
    let mut outs: Vec<Vec<Item>> = vec![vec![]; cats.len().max(1)];
    for (i, it) in ctx.items().iter().enumerate() {
        let m = require_model(ctx, i)?;
        let text = ctx.s("inputText", i, "")?;
        let detailed = ctx.b("options.includeDetailedResults", i, false)?;
        let system = ctx.s("options.systemPromptTemplate", i, "You are highly intelligent and accurate sentiment analyzer. Analyze the sentiment of the provided text.")?;
        let ask = format!("Categories: {}\nAnswer only with JSON like {{\"sentiment\": \"<one of the categories>\"{}}}.\n\nText:\n{text}", cats.join(", "),
                          if detailed { ", \"strength\": <0 to 1>, \"confidence\": <0 to 1>" } else { "" });
        let v = parse_json(&complete(ctx, &m, &system, &ask).await?)?;
        let got = as_str(&v["sentiment"]);
        let idx = cats.iter().position(|c| c.eq_ignore_ascii_case(got.trim())).ok_or_else(|| anyhow!("The model answered with an unknown category '{got}'"))?;
        let mut j = it.json.clone();
        let mut r = json!({"category": cats[idx]});
        if detailed {
            r["strength"] = v["strength"].clone();
            r["confidence"] = v["confidence"].clone();
        }
        if !j.is_object() {
            j = json!({});
        }
        j["sentimentAnalysis"] = r;
        outs[idx].push(Item { json: j, binary: it.binary.clone(), paired_item: Some(json!({"item": i})) });
    }
    Ok(outs)
}

async fn classifier(ctx: &Ctx<'_>) -> Result<Out> {
    let cats: Vec<(String, String)> = ctx.list("categories.categories", 0)?.iter().map(|c| (as_str(&c["category"]), as_str(&c["description"]))).collect();
    if cats.is_empty() {
        return Err(anyhow!("Add at least one category"));
    }
    let other = ctx.s("options.fallback", 0, "discard")? == "other";
    let multi = ctx.b("options.multiClass", 0, false)?;
    let mut outs: Vec<Vec<Item>> = vec![vec![]; cats.len() + other as usize];
    for (i, it) in ctx.items().iter().enumerate() {
        let m = require_model(ctx, i)?;
        let text = ctx.s("inputText", i, "")?;
        let list = cats.iter().map(|(c, d)| if d.is_empty() { format!("- {c}") } else { format!("- {c}: {d}") }).collect::<Vec<_>>().join("\n");
        let ask = format!("Classify the text into {} of these categories:\n{list}\nAnswer only with JSON like {{\"categories\": [\"<category>\"]}}{}.\n\nText:\n{text}",
                          if multi { "one or more" } else { "exactly one" }, if other { ", or an empty list when none fits" } else { "" });
        let system = ctx.s("options.systemPromptTemplate", i, "Please classify the text provided by the user into one of the following categories.")?;
        let v = parse_json(&complete(ctx, &m, &system, &ask).await?)?;
        let picked: Vec<usize> = v["categories"].as_array().into_iter().flatten().filter_map(|c| cats.iter().position(|(n, _)| n.eq_ignore_ascii_case(as_str(c).trim()))).collect();
        let targets: Vec<usize> = if picked.is_empty() { if other { vec![cats.len()] } else { vec![] } } else if multi { picked } else { picked[..1].to_vec() };
        for t in targets {
            outs[t].push(Item { json: it.json.clone(), binary: it.binary.clone(), paired_item: Some(json!({"item": i})) });
        }
    }
    Ok(outs)
}

async fn extractor(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let m = require_model(ctx, i)?;
        let text = ctx.s("text", i, "")?;
        let shape = match ctx.s("schemaType", i, "fromAttributes")?.as_str() {
            "fromJson" => format!("JSON shaped like this example:\n{}", ctx.s("jsonSchemaExample", i, "{}")?),
            "manual" => format!("JSON matching this JSON schema:\n{}", ctx.s("inputSchema", i, "{}")?),
            _ => {
                let attrs = ctx.list("attributes.attributes", i)?;
                let lines = attrs.iter().map(|a| format!("- {} ({}){}: {}", as_str(&a["name"]), a["type"].as_str().unwrap_or("string"),
                                                      if a["required"] == true { ", required" } else { "" }, as_str(&a["description"]))).collect::<Vec<_>>().join("\n");
                format!("a JSON object with these attributes:\n{lines}")
            }
        };
        let system = ctx.s("options.systemPromptTemplate", i, "You are an expert extraction algorithm. Only extract relevant information from the text. If you do not know the value of an attribute asked to extract, you may omit the attribute's value.")?;
        let v = parse_json(&complete(ctx, &m, &system, &format!("Extract the information as {shape}\nAnswer only with the JSON.\n\nText:\n{text}")).await?)?;
        out.push(Item::paired(json!({"output": v}), i));
    }
    Ok(vec![out])
}

async fn summarize(ctx: &Ctx<'_>) -> Result<Out> {
    let m = require_model(ctx, 0)?;
    let text = match ctx.s("operationMode", 0, "nodeInputJson")?.as_str() {
        "documentLoader" => documents(ctx, ctx.items())?.into_iter().map(|d| d.0).collect::<Vec<_>>().join("\n\n"),
        _ => ctx.items().iter().map(|it| it.json.to_string()).collect::<Vec<_>>().join("\n\n"),
    };
    let chunk = ctx.n("chunkSize", 0, 6000.0)?.max(500.0) as usize;
    let instr = ctx.s("options.summarizationMethodAndPrompts.values.prompt", 0, "Write a concise summary of the following:")?;
    // Map-reduce: summarise chunks, then the summaries.
    let mut parts = vec![];
    for c in super::vector::chunk(&text, chunk, 0) {
        parts.push(complete(ctx, &m, "", &format!("{instr}\n\n\"{c}\"\n\nCONCISE SUMMARY:")).await?);
    }
    let summary = if parts.len() == 1 { parts.remove(0) } else { complete(ctx, &m, "", &format!("{instr}\n\n\"{}\"\n\nCONCISE SUMMARY:", parts.join("\n\n"))).await? };
    Ok(vec![vec![Item::new(json!({"response": {"text": summary}}))]])
}

async fn retrieval_qa(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let m = require_model(ctx, i)?;
        let q = prompt(ctx, i, "text")?;
        let retr = *subs(ctx, "ai_retriever").first().ok_or_else(|| anyhow!("Connect a retriever to \"{}\"", ctx.node.name))?;
        let rc = sub_ctx(ctx, retr);
        let store = *subs(&rc, "ai_vectorStore").first().ok_or_else(|| anyhow!("Connect a vector store to \"{}\"", retr.name))?;
        let k = rc.n("topK", i, 4.0)?.max(1.0) as usize;
        let docs = retrieve(&sub_ctx(&rc, store), &q, k, i).await?;
        let context = docs.iter().map(|d| as_str(&d["pageContent"])).collect::<Vec<_>>().join("\n---\n");
        let system = "Use the following pieces of context to answer the user's question. If you don't know the answer, just say that you don't know, don't try to make up an answer.";
        let answer = complete(ctx, &m, &format!("{system}\n----------------\n{context}"), &q).await?;
        out.push(Item::paired(json!({"response": answer}), i));
    }
    Ok(vec![out])
}

/// MCP Server Trigger: answers one JSON-RPC request with the tools connected to `node_name`.
/// Runs on its own thread because the engine (and the tool sub-nodes) are not Send.
pub async fn mcp_serve(app: crate::app::AppState, wf: Workflow, node_name: String, req: Value) -> Result<Option<Value>> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    std::thread::spawn(move || {
        let r = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
            Ok(rt) => rt.block_on(mcp_handle(app, wf, node_name, req)),
            Err(e) => Err(e.into()),
        };
        let _ = tx.send(r);
    });
    rx.await.map_err(|_| anyhow!("The MCP handler stopped"))?
}

async fn mcp_handle(app: crate::app::AppState, wf: Workflow, node_name: String, req: Value) -> Result<Option<Value>> {
    let Some(id) = req.get("id").cloned() else { return Ok(None) }; // a notification
    let err = |code: i64, msg: String| Ok(Some(json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": msg}})));
    let owner = app.db.owner_of("workflows", &wf.id)?;
    let mut eng = crate::engine::Engine::new(app.clone(), wf, "webhook", 0, std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)), 0)?;
    // Tools use the credentials the workflow owner may use.
    eng.user = owner;
    let Some(node) = eng.wf.node(&node_name).cloned() else { return err(-32603, "Trigger node not found".into()) };
    let ctx = Ctx { eng: &eng, node: &node, inputs: vec![vec![Item::new(json!({}))]] };
    let result = match as_str(&req["method"]).as_str() {
        "initialize" => json!({
            "protocolVersion": req["params"]["protocolVersion"].as_str().unwrap_or("2025-03-26"),
            "capabilities": {"tools": {"listChanged": false}},
            "serverInfo": {"name": "flowwolf", "version": env!("CARGO_PKG_VERSION")}
        }),
        "ping" => json!({}),
        "tools/list" => {
            let tools = tools(&ctx, 0).await?;
            json!({"tools": tools.iter().map(|t| json!({"name": t.name, "description": t.description, "inputSchema": t.schema})).collect::<Vec<_>>()})
        }
        "tools/call" => {
            let tools = tools(&ctx, 0).await?;
            let name = as_str(&req["params"]["name"]);
            let Some(t) = tools.iter().find(|t| t.name == name) else { return err(-32602, format!("Unknown tool {name}")) };
            let text = call_tool(&ctx, t, &req["params"]["arguments"], 0).await;
            json!({"content": [{"type": "text", "text": text}], "isError": text.starts_with("Error:")})
        }
        other => return err(-32601, format!("Method not found: {other}")),
    };
    Ok(Some(json!({"jsonrpc": "2.0", "id": id, "result": result})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_sent_events_are_split() {
        let mut buf = "event: endpoint\r\ndata: /m?s=1\r\n\r\ndata: {\"id\":1}\n\n: ping\n\ndata: par".to_string();
        assert_eq!(sse_events(&mut buf), vec![("endpoint".to_string(), "/m?s=1".to_string()), ("message".to_string(), "{\"id\":1}".to_string())]);
        assert_eq!(buf, "data: par");
    }

    #[test]
    fn json_from_model_answers() {
        assert_eq!(parse_json("```json\n{\"a\": 1}\n```").unwrap(), json!({"a": 1}));
        assert_eq!(parse_json("Sure! Here it is: {\"sentiment\": \"Positive\"} Hope that helps").unwrap(), json!({"sentiment": "Positive"}));
        assert!(parse_json("no json").is_err());
        assert!(is_sub_node("@n8n/n8n-nodes-langchain.lmChatOpenAi") && is_sub_node("@n8n/n8n-nodes-langchain.toolCalculator") && !is_sub_node("@n8n/n8n-nodes-langchain.agent"));
    }
}
