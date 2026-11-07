//! Flow control and core data nodes.

use super::{Ctx, Out, eval_filter, eval_legacy};
use crate::engine::{Reply, run_sub};
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::time::Duration;

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    Some(match kind {
        "n8n-nodes-base.if" => if_node(ctx),
        "n8n-nodes-base.filter" => filter(ctx),
        "n8n-nodes-base.switch" => switch(ctx),
        "n8n-nodes-base.merge" => merge(ctx),
        "n8n-nodes-base.splitInBatches" => split_in_batches(ctx),
        "n8n-nodes-base.wait" => wait(ctx).await,
        "n8n-nodes-base.stopAndError" => stop_and_error(ctx),
        "n8n-nodes-base.executeWorkflow" => execute_workflow(ctx).await,
        "n8n-nodes-base.respondToWebhook" => respond(ctx),
        "n8n-nodes-base.set" => set(ctx),
        "n8n-nodes-base.code" | "n8n-nodes-base.function" | "n8n-nodes-base.functionItem" => code(ctx),
        _ => return None,
    })
}

fn condition(ctx: &Ctx, i: usize) -> Result<bool> {
    if ctx.node.type_version >= 2.0 {
        let f = ctx.val("conditions", i, json!({}))?;
        let mut f = f;
        if ctx.b("looseTypeValidation", i, false)? || ctx.b("options.looseTypeValidation", i, false)? {
            f["options"]["typeValidation"] = json!("loose");
        }
        if ctx.b("options.ignoreCase", i, false)? {
            f["options"]["caseSensitive"] = json!(false);
        }
        eval_filter(&f)
    } else {
        let any = ctx.s("combineOperation", i, "all")? == "any";
        eval_legacy(&ctx.val("conditions", i, json!({}))?, any)
    }
}

fn if_node(ctx: &Ctx) -> Result<Out> {
    let (mut yes, mut no) = (vec![], vec![]);
    for (i, it) in ctx.items().iter().enumerate() {
        let mut it = it.clone();
        it.paired_item = Some(json!({"item": i}));
        if condition(ctx, i)? { yes.push(it) } else { no.push(it) }
    }
    Ok(vec![yes, no])
}

fn filter(ctx: &Ctx) -> Result<Out> {
    let (mut kept, mut discarded) = (vec![], vec![]);
    for (i, it) in ctx.items().iter().enumerate() {
        if condition(ctx, i)? { kept.push(it.clone()) } else { discarded.push(it.clone()) }
    }
    if ctx.b("options.allowFailingItems", 0, false)? || ctx.node.raw("options.discardedOutput").is_some() {
        return Ok(vec![kept, discarded]);
    }
    Ok(vec![kept])
}

pub fn switch_outputs(node: &Node) -> usize {
    if node.raw("mode").and_then(|v| v.as_str()) == Some("expression") {
        return node.raw("numberOutputs").and_then(as_f64).unwrap_or(4.0) as usize;
    }
    let rules = node.raw("rules.values").and_then(|v| v.as_array()).map_or(0, |a| a.len());
    let extra = node.raw("options.fallbackOutput").and_then(|v| v.as_str()) == Some("extra");
    rules + extra as usize
}

fn switch(ctx: &Ctx) -> Result<Out> {
    let n = switch_outputs(ctx.node);
    let mut out: Out = vec![vec![]; n.max(1)];
    let expression_mode = ctx.s("mode", 0, "rules")? == "expression";
    for (i, it) in ctx.items().iter().enumerate() {
        if expression_mode {
            let idx = ctx.n("output", i, 0.0)? as usize;
            if idx >= n {
                return Err(anyhow!("The output {idx} is not allowed. It has to be between 0 and {}!", n.saturating_sub(1)));
            }
            out[idx].push(it.clone());
            continue;
        }
        let rules = ctx.list("rules.values", i)?;
        let all = ctx.b("options.allMatchingOutputs", i, false)?;
        let ignore_case = ctx.b("options.ignoreCase", i, false)?;
        let mut matched = false;
        for (r, rule) in rules.iter().enumerate() {
            let mut f = rule["conditions"].clone();
            if ignore_case {
                f["options"]["caseSensitive"] = json!(false);
            }
            if eval_filter(&f)? {
                out[r].push(it.clone());
                matched = true;
                if !all {
                    break;
                }
            }
        }
        if !matched {
            match ctx.val("options.fallbackOutput", i, json!("none"))? {
                Value::String(s) if s == "extra" => out[rules.len()].push(it.clone()),
                v => {
                    if let Some(idx) = as_f64(&v).map(|f| f as usize).filter(|x| *x < out.len()) {
                        out[idx].push(it.clone());
                    }
                }
            }
        }
    }
    Ok(out)
}

fn key_of(j: &Value, fields: &[String]) -> Option<String> {
    let vals: Vec<&Value> = fields.iter().map(|f| get_path(j, f).unwrap_or(&Value::Null)).collect();
    if vals.iter().all(|v| v.is_null()) { None } else { Some(serde_json::to_string(&vals).unwrap()) }
}

fn merged(a: &Value, b: &Value, prefer_b: bool) -> Value {
    let mut o = if prefer_b { a.clone() } else { b.clone() };
    let src = if prefer_b { b } else { a };
    if let (Some(o), Some(s)) = (o.as_object_mut(), src.as_object()) {
        for (k, v) in s {
            o.insert(k.clone(), v.clone());
        }
    }
    o
}

fn merge(ctx: &Ctx) -> Result<Out> {
    let mode = ctx.s("mode", 0, "append")?;
    let (a, b) = (ctx.input(0), ctx.input(1));
    let prefer_b = ctx.s("options.clashHandling.values.resolveClash", 0, "preferInput2")? != "preferInput1";
    Ok(vec![match mode.as_str() {
        "append" => ctx.inputs.iter().flatten().cloned().collect(),
        "chooseBranch" => {
            let which = ctx.n("useDataOfInput", 0, 1.0)? as usize;
            if ctx.s("output", 0, "specifiedInput")? == "empty" {
                vec![Item::new(json!({}))]
            } else {
                ctx.input(which.saturating_sub(1))
            }
        }
        "passThrough" => ctx.input(if ctx.s("output", 0, "input1")? == "input2" { 1 } else { 0 }),
        "wait" => vec![Item::new(json!({}))],
        _ => {
            let by = ctx.s("combineBy", 0, "")?;
            let legacy = ctx.s("combinationMode", 0, "")?;
            let by = if !by.is_empty() {
                by
            } else {
                match (mode.as_str(), legacy.as_str()) {
                    ("mergeByIndex", _) | (_, "mergeByPosition") => "combineByPosition".into(),
                    ("multiplex", _) | (_, "multiplex") => "combineAll".into(),
                    _ => "combineByFields".into(),
                }
            };
            match by.as_str() {
                "combineByPosition" => {
                    let include_unpaired = ctx.b("options.includeUnpaired", 0, false)?;
                    let n = if include_unpaired { a.len().max(b.len()) } else { a.len().min(b.len()) };
                    (0..n)
                        .map(|i| {
                            let ja = a.get(i).map(|x| x.json.clone()).unwrap_or(json!({}));
                            let jb = b.get(i).map(|x| x.json.clone()).unwrap_or(json!({}));
                            Item::paired(merged(&ja, &jb, prefer_b), i)
                        })
                        .collect()
                }
                "combineAll" => a.iter().flat_map(|x| b.iter().map(|y| Item::new(merged(&x.json, &y.json, prefer_b)))).collect(),
                _ => merge_by_fields(ctx, &a, &b, prefer_b)?,
            }
        }
    }])
}

fn merge_by_fields(ctx: &Ctx, a: &[Item], b: &[Item], prefer_b: bool) -> Result<Vec<Item>> {
    let (f1, f2): (Vec<String>, Vec<String>) = if let Some(s) = ctx.get("fieldsToMatchString", 0)?.filter(|v| !as_str(v).is_empty()) {
        let f: Vec<String> = as_str(&s).split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect();
        (f.clone(), f)
    } else {
        let pairs = ctx.list("mergeByFields.values", 0)?;
        let pairs = if pairs.is_empty() { ctx.list("advancedFields.values", 0)? } else { pairs };
        pairs.iter().map(|p| (as_str(&p["field1"]), as_str(&p["field2"]))).unzip()
    };
    if f1.is_empty() {
        return Err(anyhow!("You need to define at least one pair of fields to match"));
    }
    let join = ctx.s("joinMode", 0, "keepMatches")?;
    let out_from = ctx.s("outputDataFrom", 0, "both")?;
    let kb: Vec<Option<String>> = b.iter().map(|x| key_of(&x.json, &f2)).collect();
    let mut b_used = vec![false; b.len()];
    let mut out = vec![];
    for x in a {
        let k = key_of(&x.json, &f1);
        let matches: Vec<usize> = (0..b.len()).filter(|j| k.is_some() && kb[*j] == k).collect();
        matches.iter().for_each(|j| b_used[*j] = true);
        match join.as_str() {
            "keepMatches" | "keepEverything" | "enrichInput1" => {
                if matches.is_empty() {
                    if join != "keepMatches" {
                        out.push(x.clone());
                    }
                    continue;
                }
                for j in &matches {
                    out.push(Item::new(match out_from.as_str() {
                        "input1" => x.json.clone(),
                        "input2" => b[*j].json.clone(),
                        _ => merged(&x.json, &b[*j].json, prefer_b),
                    }));
                    if out_from == "input1" {
                        break;
                    }
                }
            }
            "enrichInput2" => {}
            "keepNonMatches" => {
                if matches.is_empty() && out_from != "input2" {
                    out.push(x.clone());
                }
            }
            _ => return Err(anyhow!("Unknown join mode: {join}")),
        }
    }
    match join.as_str() {
        "keepEverything" => out.extend(b.iter().enumerate().filter(|(j, _)| !b_used[*j]).map(|(_, y)| y.clone())),
        "keepNonMatches" if out_from != "input1" => out.extend(b.iter().enumerate().filter(|(j, _)| !b_used[*j]).map(|(_, y)| y.clone())),
        "enrichInput2" => {
            let ka: Vec<Option<String>> = a.iter().map(|x| key_of(&x.json, &f1)).collect();
            for (j, y) in b.iter().enumerate() {
                let m: Vec<&Item> = a.iter().enumerate().filter(|(i, _)| kb[j].is_some() && ka[*i] == kb[j]).map(|(_, x)| x).collect();
                if m.is_empty() {
                    out.push(y.clone());
                }
                for x in m {
                    out.push(Item::new(merged(&x.json, &y.json, prefer_b)));
                }
            }
        }
        _ => {}
    }
    Ok(out)
}

/// Loop Over Items (v3): output 0 = done, output 1 = loop.
fn split_in_batches(ctx: &Ctx) -> Result<Out> {
    let size = ctx.n("batchSize", 0, 1.0)?.max(1.0) as usize;
    let reset = ctx.b("options.reset", 0, false)?;
    let key = ctx.node.name.clone();
    let mut states = ctx.eng.node_state.borrow_mut();
    let fresh = reset || !states.contains_key(&key);
    let st = states.entry(key.clone()).or_insert_with(|| json!({"items": [], "processed": []}));
    if fresh {
        *st = json!({"items": ctx.items(), "processed": []});
    } else {
        let p = st["processed"].as_array_mut().unwrap();
        p.extend(ctx.items().iter().map(|x| serde_json::to_value(x).unwrap()));
    }
    let remaining: Vec<Item> = serde_json::from_value(st["items"].take()).unwrap_or_default();
    if remaining.is_empty() {
        let done: Vec<Item> = serde_json::from_value(st["processed"].take()).unwrap_or_default();
        states.remove(&key);
        return Ok(vec![done, vec![]]);
    }
    let n = size.min(remaining.len());
    let batch = remaining[..n].to_vec();
    st["items"] = serde_json::to_value(&remaining[n..])?;
    Ok(vec![vec![], batch])
}

async fn wait(ctx: &Ctx<'_>) -> Result<Out> {
    let resume = ctx.s("resume", 0, "timeInterval")?;
    let ms: i64 = match resume.as_str() {
        "specificTime" => {
            let t = ctx.s("dateTime", 0, "")?;
            let at = super::data::parse_date(&t).ok_or_else(|| anyhow!("Invalid date: {t}"))?;
            at.timestamp_millis() - now_ms()
        }
        "timeInterval" => {
            let amount = ctx.n("amount", 0, 1.0)?;
            let unit = ctx.s("unit", 0, if ctx.node.type_version >= 1.1 { "seconds" } else { "hours" })?;
            let f = match unit.as_str() {
                "minutes" => 60_000.0,
                "hours" => 3_600_000.0,
                "days" => 86_400_000.0,
                _ => 1000.0,
            };
            (amount * f) as i64
        }
        "webhook" | "form" => return wait_for_call(ctx).await,
        other => return Err(anyhow!("Unknown resume mode \"{other}\"")),
    };
    // Like n8n: long waits are persisted and the thread is released; short ones sleep in memory.
    if ms > 65_000 && ctx.eng.mode != "manual" && ctx.eng.depth == 0 {
        return Err(crate::engine::Park { kind: "time".into(), resume_at: Some(now_ms() + ms) }.into());
    }
    let deadline = now_ms() + ms.max(0);
    while now_ms() < deadline {
        if ctx.eng.cancel.load(std::sync::atomic::Ordering::Relaxed) {
            return Err(anyhow!("Execution was canceled"));
        }
        tokio::time::sleep(Duration::from_millis((deadline - now_ms()).clamp(0, 500) as u64)).await;
    }
    Ok(vec![ctx.input(0)])
}

/// Parks the execution until its resume URL (webhook or form) is called.
async fn wait_for_call(ctx: &Ctx<'_>) -> Result<Out> {
    if ctx.eng.depth > 0 {
        return Err(anyhow!("A Wait node that waits for a webhook or form cannot run inside a sub-workflow"));
    }
    let resume_at = if ctx.b("limitWaitTime", 0, false)? {
        let f = match ctx.s("resumeUnit", 0, "hours")?.as_str() {
            "minutes" => 60_000.0,
            "hours" => 3_600_000.0,
            "days" => 86_400_000.0,
            _ => 1000.0,
        };
        Some(now_ms() + (ctx.n("resumeAmount", 0, 1.0)? * f) as i64)
    } else {
        None
    };
    let kind = if ctx.s("resume", 0, "webhook")? == "form" { "form" } else { "webhook" };
    Err(crate::engine::Park { kind: kind.into(), resume_at }.into())
}

fn stop_and_error(ctx: &Ctx) -> Result<Out> {
    if ctx.s("errorType", 0, "errorMessage")? == "errorObject" {
        let o = ctx.val("errorObject", 0, json!({}))?;
        let o = if let Value::String(s) = &o { serde_json::from_str(s).unwrap_or(o.clone()) } else { o };
        return Err(anyhow!("{}", o.get("message").map(as_str).unwrap_or_else(|| o.to_string())));
    }
    Err(anyhow!("{}", ctx.s("errorMessage", 0, "")?))
}

async fn execute_workflow(ctx: &Ctx<'_>) -> Result<Out> {
    let source = ctx.s("source", 0, "database")?;
    let wf: Workflow = match source.as_str() {
        "parameter" => {
            let j = ctx.val("workflowJson", 0, json!({}))?;
            let j = if let Value::String(s) = &j { serde_json::from_str(s)? } else { j };
            serde_json::from_value(j)?
        }
        "database" => {
            let id = ctx.s("workflowId", 0, "")?;
            ctx.eng.app.db.workflow(&id)?.ok_or_else(|| anyhow!("Workflow \"{id}\" does not exist"))?
        }
        other => return Err(anyhow!("Source \"{other}\" is not supported")),
    };
    let wait = ctx.b("options.waitForSubWorkflow", 0, true)?;
    let each = ctx.s("mode", 0, "once")? == "each";
    let batches: Vec<Vec<Item>> = if each { ctx.items().iter().map(|i| vec![i.clone()]).collect() } else { vec![ctx.input(0)] };
    let mut out = vec![];
    for (i, b) in batches.into_iter().enumerate() {
        if !wait {
            // Fire and forget: runs as an independent execution.
            let l = crate::engine::Launch { wf: wf.clone(), mode: "integrated".into(), spec: crate::engine::StartSpec { items: Some(b), ..Default::default() }, reply: None, retry_of: None, user: None, exec_id: None };
            crate::engine::launch(ctx.eng.app.clone(), l)?;
            continue;
        }
        let res = run_sub(ctx.eng, wf.clone(), b).await?;
        out.extend(res.into_iter().map(|mut x| {
            if each {
                x.paired_item = Some(json!({"item": i}));
            }
            x
        }));
    }
    if !wait {
        return Ok(vec![ctx.input(0)]);
    }
    Ok(vec![out])
}

fn respond(ctx: &Ctx) -> Result<Out> {
    let with = ctx.s("respondWith", 0, "firstIncomingItem")?;
    let code = ctx.n("options.responseCode", 0, if with == "redirect" { 307.0 } else { 200.0 })? as u16;
    let mut headers: Vec<(String, String)> = ctx
        .list("options.responseHeaders.entries", 0)?
        .iter()
        .map(|e| (as_str(&e["name"]).to_lowercase(), as_str(&e["value"])))
        .collect();
    let has_ct = headers.iter().any(|(k, _)| k == "content-type");
    let mut set_ct = |ct: &str| {
        if !has_ct {
            headers.push(("content-type".into(), ct.into()));
        }
    };
    let body: Vec<u8> = match with.as_str() {
        "allIncomingItems" => {
            set_ct("application/json");
            Value::Array(ctx.items().iter().map(|i| i.json.clone()).collect()).to_string().into_bytes()
        }
        "firstIncomingItem" => {
            set_ct("application/json");
            ctx.items().first().map(|i| i.json.clone()).unwrap_or(json!({})).to_string().into_bytes()
        }
        "json" => {
            set_ct("application/json");
            let v = ctx.val("responseBody", 0, json!({}))?;
            match v {
                Value::String(s) => serde_json::from_str::<Value>(&s).map_err(|e| anyhow!("Invalid JSON in 'Response Body': {e}"))?.to_string(),
                v => v.to_string(),
            }
            .into_bytes()
        }
        "text" => {
            set_ct("text/plain; charset=utf-8");
            ctx.s("responseBody", 0, "")?.into_bytes()
        }
        "redirect" => {
            headers.push(("location".into(), ctx.s("redirectURL", 0, "")?));
            vec![]
        }
        "binary" => {
            let prop = ctx.s("inputFieldName", 0, "data")?;
            let bin = ctx.items().first().and_then(|i| i.binary.as_ref()).and_then(|b| b.get(&prop)).cloned().ok_or_else(|| anyhow!("No binary data property \"{prop}\" found"))?;
            set_ct(bin["mimeType"].as_str().unwrap_or("application/octet-stream"));
            super::data::binary_bytes(&bin)?
        }
        _ => vec![],
    };
    match ctx.eng.reply.borrow_mut().take() {
        Some(tx) => {
            let _ = tx.send(Reply { status: code, headers, body });
        }
        None if ctx.eng.mode == "manual" || ctx.eng.mode == "integrated" => {}
        None => return Err(anyhow!("No webhook is waiting for a response (the workflow must start with a Webhook node set to 'Using Respond to Webhook node')")),
    }
    Ok(vec![ctx.input(0)])
}

fn convert(v: Value, t: &str, ignore_errors: bool) -> Result<Value> {
    let fail = |v: &Value| if ignore_errors { Ok(v.clone()) } else { Err(anyhow!("'{}' cannot be converted to {t}", as_str(v))) };
    Ok(match t {
        "number" | "numberValue" => match &v {
            Value::Number(_) => v,
            Value::Null => v,
            _ => match as_f64(&v) {
                Some(f) if f.fract() == 0.0 && f.abs() < 9e15 => json!(f as i64),
                Some(f) => json!(f),
                None => return fail(&v),
            },
        },
        "boolean" | "booleanValue" => match &v {
            Value::Bool(_) => v,
            Value::String(s) => match s.to_lowercase().as_str() {
                "true" | "1" | "yes" => json!(true),
                "false" | "0" | "no" | "" => json!(false),
                _ => return fail(&v),
            },
            _ => json!(truthy(&v)),
        },
        "array" | "arrayValue" | "object" | "objectValue" => match &v {
            Value::String(s) => match serde_json::from_str::<Value>(s) {
                Ok(p) if (t.starts_with("array") && p.is_array()) || (t.starts_with("object") && p.is_object()) => p,
                _ => return fail(&v),
            },
            _ => v,
        },
        "string" | "stringValue" => match v {
            Value::String(_) => v,
            Value::Null => json!(""),
            other => json!(as_str(&other)),
        },
        _ => v,
    })
}

fn set(ctx: &Ctx) -> Result<Out> {
    let v = ctx.node.type_version;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let dot = ctx.b("options.dotNotation", i, true)?;
        let ignore = ctx.b("options.ignoreConversionErrors", i, false)?;
        let mut fields: Vec<(String, Value)> = vec![];
        let mut base_keep = "all".to_string();
        if v < 3.0 {
            for (t, list) in [("string", "values.string"), ("number", "values.number"), ("boolean", "values.boolean")] {
                for e in ctx.list(list, i)? {
                    fields.push((as_str(&e["name"]), convert(e["value"].clone(), t, true)?));
                }
            }
            if ctx.b("keepOnlySet", i, false)? {
                base_keep = "none".into();
            }
        } else if ctx.s("mode", i, "manual")? == "raw" {
            let raw = ctx.val("jsonOutput", i, json!({}))?;
            let obj = match raw {
                Value::String(s) => serde_json::from_str::<Value>(&s).map_err(|e| anyhow!("'JSON Output' is not valid JSON: {e}"))?,
                v => v,
            };
            for (k, x) in obj.as_object().cloned().unwrap_or_default() {
                fields.push((k, x));
            }
        } else if ctx.node.raw("assignments").is_some() || v >= 3.3 {
            for a in ctx.list("assignments.assignments", i)? {
                let t = a["type"].as_str().unwrap_or("string");
                fields.push((as_str(&a["name"]), convert(a["value"].clone(), t, ignore)?));
            }
        } else {
            for f in ctx.list("fields.values", i)? {
                let t = f["type"].as_str().unwrap_or("stringValue");
                fields.push((as_str(&f["name"]), convert(f[t].clone(), t, ignore)?));
            }
        }
        if v >= 3.0 {
            base_keep = if v >= 3.3 && !ctx.b("includeOtherFields", i, false)? { "none".into() } else { ctx.s("include", i, "all")? };
        }
        let field_list = |p: &str| -> Result<Vec<String>> {
            Ok(ctx.s(p, i, "")?.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect())
        };
        let mut json = match base_keep.as_str() {
            "none" => json!({}),
            "selected" => {
                let mut o = json!({});
                for f in field_list("includeFields")? {
                    if let Some(x) = get_path(&it.json, &f) {
                        set_path(&mut o, &f, x.clone());
                    }
                }
                o
            }
            "except" => {
                let mut o = it.json.clone();
                for f in field_list("excludeFields")? {
                    remove_path(&mut o, &f);
                }
                o
            }
            _ => it.json.clone(),
        };
        if !json.is_object() {
            json = json!({});
        }
        for (k, x) in fields {
            if dot { set_path(&mut json, &k, x) } else { json.as_object_mut().unwrap().insert(k, x); }
        }
        let mut item = Item::paired(json, i);
        if ctx.b("options.includeBinary", i, true)? && base_keep != "none" {
            item.binary = it.binary.clone();
        }
        out.push(item);
    }
    Ok(vec![out])
}

fn normalize_code_output(ctx: &Ctx, res: Value, each: bool) -> Result<Out> {
    let arr = match res {
        Value::Array(a) => a,
        Value::Null => vec![],
        Value::Object(_) => vec![res],
        other => return Err(anyhow!("Code doesn't return items properly. Got: {other}")),
    };
    let mut out = vec![];
    for (i, v) in arr.into_iter().enumerate() {
        let mut item = match v {
            Value::Object(ref m) if m.get("json").is_some_and(|j| j.is_object()) => serde_json::from_value::<Item>(v.clone()).unwrap_or_else(|_| Item::new(v)),
            Value::Object(_) => Item::new(v),
            other => return Err(anyhow!("A 'json' property isn't an object [item {i}]: {other}")),
        };
        if item.paired_item.is_none() {
            item.paired_item = Some(json!({"item": if each { i } else { i.min(ctx.items().len().saturating_sub(1)) }}));
        }
        out.push(item);
    }
    Ok(vec![out])
}

#[cfg(feature = "python")]
fn python_code(ctx: &Ctx) -> Result<Out> {
    let each = ctx.s("mode", 0, "runOnceForAllItems")? == "runOnceForEachItem";
    let src = ctx.node.raw("pythonCode").map(as_str).unwrap_or_default();
    let items = serde_json::to_string(ctx.items())?;
    let timeout = ctx.eng.app.cfg.code_timeout_ms;
    // Linux: isolated runner process for everyone. Elsewhere there is no sandbox, so only owners/admins.
    let (res, logs) = if cfg!(target_os = "linux") {
        crate::python::run_isolated(&src, &items, each, timeout)?
    } else {
        let allowed = match &ctx.eng.user {
            Some(u) => ctx.eng.app.db.is_admin(u)?,
            None => false,
        };
        if !allowed {
            return Err(anyhow!("Python in the Code node needs the Linux sandbox; on this platform it is limited to owners and admins"));
        }
        crate::python::run(&src, &items, each, timeout)?
    };
    for l in logs {
        tracing::info!(execution = ctx.eng.exec_id, "python: {l}");
        ctx.eng.app.push(json!({"type": "console", "data": {"executionId": ctx.eng.exec_id.to_string(), "message": l}}));
        ctx.eng.logs.borrow_mut().push(l);
    }
    normalize_code_output(ctx, res, each)
}

#[cfg(not(feature = "python"))]
fn python_code(_: &Ctx) -> Result<Out> {
    Err(anyhow!("Python is not included in this build. Use the flowwolf:python image (cargo feature \"python\")"))
}

fn code(ctx: &Ctx) -> Result<Out> {
    let (src, each) = match ctx.node.kind.as_str() {
        "n8n-nodes-base.function" => (ctx.s("functionCode", 0, "return items;")?, false),
        "n8n-nodes-base.functionItem" => (format!("{}\nreturn item;", ctx.s("functionCode", 0, "")?), true),
        _ => {
            let lang = ctx.s("language", 0, "javaScript")?;
            if lang == "python" || lang == "pythonNative" {
                return python_code(ctx);
            }
            if lang != "javaScript" {
                return Err(anyhow!("Unsupported language \"{lang}\""));
            }
            let each = ctx.s("mode", 0, "runOnceForAllItems")? == "runOnceForEachItem";
            // Raw source: the Code node parameter is code, not an expression.
            let src = ctx.node.raw("jsCode").map(as_str).unwrap_or_default();
            (src, each)
        }
    };
    let js = &ctx.eng.js;
    js.set_static(&ctx.eng.static_data.borrow())?;
    let res = js.run_code(&src, each, ctx.eng.app.cfg.code_timeout_ms)?;
    *ctx.eng.static_data.borrow_mut() = js.get_static()?;
    normalize_code_output(ctx, res, each)
}
