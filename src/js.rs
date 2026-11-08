//! Embedded QuickJS sandbox for n8n style expressions (`={{ $json.x }}`) and the Code node.
//! One runtime per execution; data of other nodes is pulled lazily through native hooks.

use anyhow::{Result, anyhow};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use rquickjs::{Context, Function, Runtime};
use serde_json::Value;
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicI64, Ordering};

const PRELUDE: &str = include_str!("prelude.js");

pub struct Hooks {
    /// (node, branch, run) -> JSON array of items, "!" = not executed, "!!" = unknown node
    pub node_out: Rc<dyn Fn(&str, usize, i64) -> String>,
    pub node_params: Rc<dyn Fn(&str) -> String>,
    pub env: Rc<dyn Fn() -> String>,
    pub vars: Rc<dyn Fn() -> String>,
    pub log: Rc<dyn Fn(String)>,
}

pub struct Js {
    rt: Runtime,
    ctx: Context,
    deadline: Arc<AtomicI64>,
}

fn now() -> i64 {
    crate::model::now_ms()
}

/// The zone expressions use when none is given: GENERIC_TIMEZONE / TZ, else UTC.
fn default_zone() -> String {
    ["GENERIC_TIMEZONE", "TZ"].iter().find_map(|k| std::env::var(k).ok()).filter(|z| z.parse::<chrono_tz::Tz>().is_ok()).unwrap_or_else(|| "UTC".into())
}

/// UTC offset in minutes of `zone` at the instant `ms` (unknown zones count as UTC).
fn tz_offset_minutes(zone: &str, ms: f64) -> i32 {
    use chrono::{Offset, TimeZone};
    let tz: chrono_tz::Tz = zone.parse().unwrap_or(chrono_tz::UTC);
    match chrono::Utc.timestamp_millis_opt(ms as i64).single() {
        Some(t) => tz.offset_from_utc_datetime(&t.naive_utc()).fix().local_minus_utc() / 60,
        None => 0,
    }
}

impl Js {
    pub fn new(h: Hooks) -> Result<Js> {
        let rt = Runtime::new()?;
        rt.set_memory_limit(512 * 1024 * 1024);
        rt.set_max_stack_size(4 * 1024 * 1024);
        let deadline = Arc::new(AtomicI64::new(i64::MAX));
        let d = deadline.clone();
        rt.set_interrupt_handler(Some(Box::new(move || now() > d.load(Ordering::Relaxed))));
        let ctx = Context::full(&rt)?;
        ctx.with(|ctx| -> rquickjs::Result<()> {
            let g = ctx.globals();
            let f = h.node_out.clone();
            g.set("__nodeOut", Function::new(ctx.clone(), move |n: String, b: i32, r: i32| f(&n, b.max(0) as usize, r as i64))?)?;
            let f = h.node_params.clone();
            g.set("__nodeParams", Function::new(ctx.clone(), move |n: String| f(&n))?)?;
            let f = h.env.clone();
            g.set("__env", Function::new(ctx.clone(), move || f())?)?;
            let f = h.vars.clone();
            g.set("__vars", Function::new(ctx.clone(), move || f())?)?;
            let f = h.log.clone();
            g.set("__log", Function::new(ctx.clone(), move |s: String| f(s))?)?;
            g.set("__b64e", Function::new(ctx.clone(), |s: String| B64.encode(s.as_bytes()))?)?;
            g.set(
                "__b64d",
                Function::new(ctx.clone(), |s: String| {
                    B64.decode(s.trim()).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default()
                })?,
            )?;
            // Time zones for DateTime / $now: offset of a zone at an instant, and the instance's zone
            // (GENERIC_TIMEZONE, like Luxon's default zone in n8n).
            g.set("__tzOffset", Function::new(ctx.clone(), |zone: String, ms: f64| tz_offset_minutes(&zone, ms))?)?;
            g.set("__tzDefault", Function::new(ctx.clone(), default_zone)?)?;
            ctx.eval::<(), _>(PRELUDE)?;
            Ok(())
        })
        .map_err(|e| anyhow!("js init: {e}"))?;
        Ok(Js { rt, ctx, deadline })
    }

    fn call(&self, func: &str, args: Vec<String>, timeout_ms: i64) -> Result<Value> {
        self.deadline.store(now() + timeout_ms, Ordering::Relaxed);
        let out = self.ctx.with(|ctx| -> rquickjs::Result<String> {
            let f: Function = ctx.globals().get(func)?;
            match args.len() {
                0 => f.call(()),
                1 => f.call((args[0].as_str(),)),
                2 => f.call((args[0].as_str(), args[1].as_str())),
                _ => f.call((args[0].as_str(), args[1].as_str(), args[2].as_str())),
            }
        });
        self.deadline.store(i64::MAX, Ordering::Relaxed);
        let out = out.map_err(|e| match e {
            rquickjs::Error::Exception => anyhow!("Script was interrupted (timeout or memory limit)"),
            e => anyhow!("{e}"),
        })?;
        unwrap_result(&out)
    }

    /// Loads the input items and context of the node that is about to run.
    pub fn set_input(&self, items_json: &str, ctx_json: &str) -> Result<()> {
        self.call("__setInput", vec![items_json.into(), ctx_json.into()], 5_000).map(|_| ())
    }

    /// Evaluates one expression body (without the braces) for item `idx`.
    pub fn eval(&self, code: &str, idx: usize) -> Result<Value> {
        self.call("__evs", vec![code.into(), idx.to_string()], 10_000)
    }

    pub fn set_static(&self, data: &Value) -> Result<()> {
        self.call("__setStatic", vec![data.to_string()], 1_000).map(|_| ())
    }

    pub fn get_static(&self) -> Result<Value> {
        self.call("__getStatic", vec![], 1_000)
    }

    /// Runs Code node source. `each` = runOnceForEachItem. Returns the raw returned value.
    pub fn run_code(&self, code: &str, each: bool, timeout_ms: i64) -> Result<Value> {
        self.deadline.store(now() + timeout_ms, Ordering::Relaxed);
        let start = self.ctx.with(|ctx| -> rquickjs::Result<()> {
            let f: Function = ctx.globals().get("__startCode")?;
            f.call((code, each))
        });
        let mut res = start.map_err(|e| anyhow!("{e}"));
        if res.is_ok() {
            // Drive promises until the async wrapper settles.
            while self.rt.is_job_pending() {
                if self.rt.execute_pending_job().is_err() {
                    break;
                }
            }
            res = Ok(());
        }
        self.deadline.store(i64::MAX, Ordering::Relaxed);
        res?;
        self.call("__codeResult", vec![], 1_000)
    }
}

fn unwrap_result(s: &str) -> Result<Value> {
    let v: Value = serde_json::from_str(s).unwrap_or(Value::Null);
    if let Some(e) = v.get("e") {
        return Err(anyhow!("{}", crate::model::as_str(e)));
    }
    Ok(v.get("v").cloned().unwrap_or(Value::Null))
}

#[derive(Debug, PartialEq)]
pub enum Part {
    Text(String),
    Code(String),
}

/// Splits `Hello {{ $json.name }}!` into text and code parts. Braces inside
/// the code (object literals) and string literals are respected.
pub fn parse_template(s: &str) -> Vec<Part> {
    let b = s.as_bytes();
    let mut parts = vec![];
    let mut text = String::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'{' && b.get(i + 1) == Some(&b'{') {
            if let Some(end) = find_close(b, i + 2) {
                if !text.is_empty() {
                    parts.push(Part::Text(std::mem::take(&mut text)));
                }
                parts.push(Part::Code(s[i + 2..end].trim().to_string()));
                i = end + 2;
                continue;
            }
        }
        let ch = s[i..].chars().next().unwrap();
        text.push(ch);
        i += ch.len_utf8();
    }
    if !text.is_empty() {
        parts.push(Part::Text(text));
    }
    parts
}

fn find_close(b: &[u8], mut j: usize) -> Option<usize> {
    let mut depth = 0i32;
    let mut quote: Option<u8> = None;
    while j < b.len() {
        let c = b[j];
        if let Some(q) = quote {
            if c == b'\\' {
                j += 2;
                continue;
            }
            if c == q {
                quote = None;
            }
        } else {
            match c {
                b'\'' | b'"' | b'`' => quote = Some(c),
                b'{' => depth += 1,
                b'}' if depth > 0 => depth -= 1,
                b'}' if b.get(j + 1) == Some(&b'}') => return Some(j),
                _ => {}
            }
        }
        j += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn js() -> Js {
        Js::new(Hooks {
            node_out: Rc::new(|n, _, _| if n == "A" { r#"[{"json":{"v":42}}]"#.into() } else { "!!".into() }),
            node_params: Rc::new(|_| "{}".into()),
            env: Rc::new(|| "{}".into()),
            vars: Rc::new(|| r#"{"k":"v"}"#.into()),
            log: Rc::new(|_| {}),
        })
        .unwrap()
    }

    #[test]
    fn templates() {
        assert_eq!(parse_template("a {{ {x:1}.x }} b"), vec![
            Part::Text("a ".into()),
            Part::Code("{x:1}.x".into()),
            Part::Text(" b".into())
        ]);
        assert_eq!(parse_template("{{ '}}' }}"), vec![Part::Code("'}}'".into())]);
    }

    #[test]
    fn date_time_zones() {
        let j = js();
        j.set_input(r#"[{"json":{}}]"#, r#"{"runIndex":0,"workflow":{},"execution":{}}"#).unwrap();
        let e = |code: &str| j.eval(code, 0).unwrap();
        let b = "DateTime.fromISO('2026-11-08T01:16:26.174Z').setZone('Europe/Berlin')";
        assert_eq!(e(&format!("{b}.toISO()")), json!("2026-11-08T02:16:26.174+01:00"));
        assert_eq!(e(&format!("[{b}.hour, {b}.day, {b}.zoneName, {b}.offset]")), json!([2, 8, "Europe/Berlin", 60]));
        // summer time
        assert_eq!(e("DateTime.fromISO('2026-07-01T00:00:00Z').setZone('Europe/Berlin').toISO()"), json!("2026-07-01T02:00:00.000+02:00"));
        // start of the day in the zone, not in UTC
        assert_eq!(e("DateTime.fromISO('2026-11-07T23:30:00Z').setZone('Europe/Berlin').startOf('day').toISO()"), json!("2026-11-08T00:00:00.000+01:00"));
        // a calendar day across the DST switch keeps the wall time
        assert_eq!(e("DateTime.fromISO('2026-03-28T12:00:00', {zone: 'Europe/Berlin'}).plus({days: 1}).toISO()"), json!("2026-03-29T12:00:00.000+02:00"));
        assert_eq!(e("DateTime.fromISO('2026-03-28T12:00:00', {zone: 'Europe/Berlin'}).plus({hours: 24}).toISO()"), json!("2026-03-29T13:00:00.000+02:00"));
        // wall time without offset is read in the zone; formatting and UTC
        assert_eq!(e("DateTime.fromISO('2026-11-08 09:05', {zone: 'Europe/Berlin'}).toUTC().toISO()"), json!("2026-11-08T08:05:00.000Z"));
        assert_eq!(e("DateTime.fromISO('2026-11-08T09:05:00Z').setZone('America/New_York').toFormat('dd.MM.yyyy HH:mm ZZ z')"), json!("08.11.2026 04:05 -05:00 America/New_York"));
        assert_eq!(e("DateTime.utc(2026, 1, 2, 3).toISO()"), json!("2026-01-02T03:00:00.000Z"));
        assert_eq!(e("DateTime.fromISO('nope').isValid"), json!(false));
    }

    #[test]
    fn expressions_and_code() {
        let j = js();
        j.set_input(r#"[{"json":{"name":"Ada","n":[1,2,3]}}]"#, r#"{"runIndex":0,"workflow":{},"execution":{}}"#).unwrap();
        assert_eq!(j.eval("$json.name.toUpperCase()", 0).unwrap(), json!("ADA"));
        assert_eq!(j.eval("$json.n.sum()", 0).unwrap(), json!(6));
        assert_eq!(j.eval("$('A').item.json.v", 0).unwrap(), json!(42));
        assert_eq!(j.eval("$vars.k", 0).unwrap(), json!("v"));
        assert_eq!(j.eval("DateTime.fromISO('2024-01-31T10:00:00Z').toFormat('yyyy')", 0).unwrap(), json!("2024"));
        assert!(j.eval("$('Missing').item", 0).is_err());
        let out = j.run_code("return items.map(i => ({json: {x: i.json.name + '!'}}))", false, 1000).unwrap();
        assert_eq!(out, json!([{"json": {"x": "Ada!"}}]));
        let out = j.run_code("$json.y = 1; return $input.item;", true, 1000).unwrap();
        assert_eq!(out[0]["json"]["y"], json!(1));
        assert!(j.run_code("while(true){}", false, 200).is_err());
        assert!(j.run_code("throw new Error('boom')", false, 1000).unwrap_err().to_string().contains("boom"));
    }
}
