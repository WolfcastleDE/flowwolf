//! AI Agent node: a tool-calling loop over any supported chat model.
//! Model, memory, tools and output parser come from connected sub-nodes like in n8n
//! (see cluster.rs). Without a model sub-node the node's own provider/model parameters and its
//! built-in "Tools" list are used (Flowwolf's compact agent).

use super::cluster::{self as cl, Api, Model, Tool, ToolRun};
use super::{Ctx, Out, rl_value};
use crate::engine::run_sub;
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::time::Duration;

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    match kind {
        "@n8n/n8n-nodes-langchain.agent" => Some(agent(ctx).await),
        _ => None,
    }
}

/// Tools from the node's own "Tools" parameter.
fn legacy_tools(ctx: &Ctx, i: usize) -> Result<Vec<Tool>> {
    let mut out = vec![];
    for t in ctx.list("tools.values", i)? {
        let name: String = as_str(&t["name"]).chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' }).collect();
        if name.is_empty() {
            return Err(anyhow!("Every tool needs a name"));
        }
        out.push(Tool { name, description: as_str(&t["description"]), schema: cl::input_schema(), run: ToolRun::Legacy(t) });
    }
    Ok(out)
}

async fn call_legacy(ctx: &Ctx<'_>, spec: &Value, input: &str) -> String {
    let res: Result<String> = async {
        match spec["type"].as_str().unwrap_or("workflow") {
            "vectorStore" => {
                let creds = ctx.creds("openAiApi").map_err(|_| anyhow!("The vector store tool needs an OpenAI (or compatible) credential for embeddings"))?;
                let model = spec["model"].as_str().filter(|m| !m.is_empty()).unwrap_or("text-embedding-3-small");
                let q = super::vector::embed(&ctx.eng.app, &creds, model, &[input.to_string()]).await?.remove(0);
                let k = as_f64(&spec["topK"]).unwrap_or(4.0).max(1.0) as usize;
                let hits = super::vector::search(&ctx.eng.app, spec["memoryKey"].as_str().unwrap_or("default"), &q, k)?;
                Ok(hits.iter().map(|h| as_str(&h["text"])).collect::<Vec<_>>().join("\n---\n"))
            }
            "http" => {
                let url = as_str(&spec["url"]).replace("{input}", &urlencode(input));
                let method = spec["method"].as_str().unwrap_or("GET").to_uppercase();
                let mut rb = ctx.eng.app.http.request(reqwest::Method::from_bytes(method.as_bytes())?, url).timeout(Duration::from_secs(60));
                if method != "GET" {
                    rb = rb.json(&json!({ "input": input }));
                }
                let r = rb.send().await?;
                let status = r.status();
                let text = r.text().await?;
                Ok(if status.is_success() { text } else { format!("HTTP {status}: {text}") })
            }
            _ => {
                let id = rl_value(&spec["workflowId"]);
                let wf = ctx.eng.app.db.workflow(&id)?.ok_or_else(|| anyhow!("Tool workflow {id} not found"))?;
                let out = run_sub(ctx.eng, wf, vec![Item::new(json!({ "query": input }))]).await?;
                Ok(serde_json::to_string(&out.into_iter().map(|i| i.json).collect::<Vec<_>>())?)
            }
        }
    }
    .await;
    // Tool errors go back to the model so it can recover.
    res.unwrap_or_else(|e| format!("Error: {e}")).chars().take(20_000).collect()
}

fn urlencode(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

/// The model from the node's own parameters (no model sub-node connected).
fn own_model(ctx: &Ctx, i: usize) -> Result<Model> {
    let provider = ctx.s("provider", i, "openAi")?;
    let opts = ctx.val("options", i, json!({}))?;
    let anthropic = provider == "anthropic";
    let c = ctx.creds(if anthropic { "anthropicApi" } else { "openAiApi" })?;
    let url = c["url"].as_str().filter(|s| !s.is_empty()).unwrap_or(if anthropic { "https://api.anthropic.com" } else { "https://api.openai.com/v1" }).trim_end_matches('/').to_string();
    Ok(Model {
        api: if anthropic { Api::Anthropic } else { Api::OpenAi },
        url,
        key: as_str(&c["apiKey"]),
        model: ctx.s("model", i, if anthropic { "claude-sonnet-5-5" } else { "gpt-4o-mini" })?,
        temperature: as_f64(&opts["temperature"]),
        max_tokens: as_f64(&opts["maxTokens"]).map(|n| n as i64),
        headers: vec![],
        query: vec![],
        aws: Value::Null,
    })
}

async fn agent(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let model = match cl::model(ctx, i)? {
            Some(m) => m,
            None => own_model(ctx, i)?,
        };
        let prompt = if ctx.s("promptType", i, "auto")? == "define" {
            ctx.s("text", i, "")?
        } else {
            ctx.items()[i].json.get("chatInput").map(as_str).ok_or_else(|| anyhow!("No 'chatInput' field in the input. Set 'Source for Prompt' to 'Define below'"))?
        };
        let opts = ctx.val("options", i, json!({}))?;
        let system = opts["systemMessage"].as_str().filter(|s| !s.is_empty()).unwrap_or("You are a helpful assistant").to_string();
        let max_iter = as_f64(&opts["maxIterations"]).unwrap_or(10.0).clamp(1.0, 50.0) as usize;
        let mut tools = legacy_tools(ctx, i)?;
        tools.extend(cl::tools(ctx, i).await?);
        let parser = cl::parser(ctx);
        let asked = match &parser {
            Some(p) => format!("{prompt}\n\n{}", p.instructions(i)?),
            None => prompt.clone(),
        };

        // Memory: a memory sub-node, or the node's own window kept in workflow static data.
        let memory = cl::memory(ctx, i)?;
        let window = as_f64(&opts["memoryWindow"]).unwrap_or(0.0) as usize;
        let mem_key = format!("agent:{}:{}", ctx.node.name, as_str(&opts["sessionKey"]));
        let mut msgs: Vec<Value> = match &memory {
            Some(m) => m.load().await?,
            None if window > 0 => ctx.eng.static_data.borrow()["global"][&mem_key].as_array().cloned().unwrap_or_default(),
            None => vec![],
        };
        let history_len = msgs.len();
        msgs.push(json!({"role": "user", "content": asked}));
        let mut steps = vec![];
        let mut answer = None;
        for _ in 0..max_iter {
            let (assistant, calls, text) = cl::chat(&ctx.eng.app, &model, &system, &msgs, &tools).await?;
            msgs.push(assistant);
            if calls.is_empty() {
                answer = Some(text);
                break;
            }
            let mut results = vec![];
            for (id, name, args) in calls {
                let res = match tools.iter().find(|t| t.name == name) {
                    Some(Tool { run: ToolRun::Legacy(spec), .. }) => {
                        let input = args.get("input").map(as_str).unwrap_or_else(|| args.to_string());
                        call_legacy(ctx, spec, &input).await
                    }
                    Some(t) => cl::call_tool(ctx, t, &args, i).await,
                    None => format!("Error: unknown tool {name}"),
                };
                steps.push(json!({"action": {"tool": name, "toolInput": args}, "observation": res}));
                results.push((id, res));
            }
            msgs.extend(cl::tool_results(model.api, results));
        }
        let answer = answer.ok_or_else(|| anyhow!("Agent stopped after {max_iter} iterations without a final answer"))?;
        match &memory {
            Some(m) => m.save(&prompt, &answer).await?,
            None if window > 0 => {
                // Keep only plain user/assistant turns so the stored history stays valid for every API.
                let mut hist: Vec<Value> = msgs[..history_len].to_vec();
                hist.push(json!({"role": "user", "content": prompt}));
                hist.push(json!({"role": "assistant", "content": answer}));
                let keep = hist.len().saturating_sub(window * 2);
                let mut sd = ctx.eng.static_data.borrow_mut();
                if !sd["global"].is_object() {
                    sd["global"] = json!({});
                }
                sd["global"][&mem_key] = Value::Array(hist[keep..].to_vec());
            }
            None => {}
        }
        let output = match &parser {
            Some(p) => p.parse(&answer, i).await?,
            None => json!(answer),
        };
        let mut j = json!({ "output": output });
        if ctx.b("options.returnIntermediateSteps", i, false)? {
            j["intermediateSteps"] = Value::Array(steps);
        }
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}
