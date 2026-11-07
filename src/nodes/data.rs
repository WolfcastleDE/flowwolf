//! Data transformation nodes.

use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use chrono::{DateTime, Datelike, FixedOffset, NaiveDate, NaiveDateTime, TimeZone, Timelike, Utc};
use serde_json::{Map, Value, json};
use std::cmp::Ordering;

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    Some(match kind {
        "n8n-nodes-base.splitOut" => split_out(ctx),
        "n8n-nodes-base.aggregate" => aggregate(ctx),
        "n8n-nodes-base.sort" => sort(ctx),
        "n8n-nodes-base.limit" => limit(ctx),
        "n8n-nodes-base.removeDuplicates" => remove_duplicates(ctx),
        "n8n-nodes-base.summarize" => summarize(ctx),
        "n8n-nodes-base.renameKeys" => rename_keys(ctx),
        "n8n-nodes-base.dateTime" => date_time(ctx),
        "n8n-nodes-base.crypto" => crypto(ctx),
        "n8n-nodes-base.html" => html(ctx),
        "n8n-nodes-base.extractFromFile" => extract_from_file(ctx),
        "n8n-nodes-base.convertToFile" => convert_to_file(ctx),
        "n8n-nodes-base.readWriteFile" => read_write_file(ctx).await,
        "n8n-nodes-base.rssFeedRead" => rss(ctx).await,
        _ => return None,
    })
}

fn fields(s: &str) -> Vec<String> {
    s.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect()
}

fn split_out(ctx: &Ctx) -> Result<Out> {
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let to_split = fields(&ctx.s("fieldToSplitOut", i, "")?);
        let include = ctx.s("include", i, "noOtherFields")?;
        let dest = ctx.s("options.destinationFieldName", i, "")?;
        let field = to_split.first().ok_or_else(|| anyhow!("'Fields To Split Out' is empty"))?;
        let arr = match get_path(&it.json, field) {
            Some(Value::Array(a)) => a.clone(),
            Some(Value::Object(o)) => o.values().cloned().collect(),
            Some(Value::Null) | None => {
                if ctx.b("options.disableDotNotation", i, false)? {
                    vec![]
                } else {
                    return Err(anyhow!("The field '{field}' wasn't found in the input item"));
                }
            }
            Some(v) => vec![v.clone()],
        };
        for v in arr {
            let mut base = match include.as_str() {
                "allOtherFields" => {
                    let mut b = it.json.clone();
                    remove_path(&mut b, field);
                    b
                }
                "selectedOtherFields" => {
                    let mut b = json!({});
                    for f in fields(&ctx.s("fieldsToInclude", i, "")?) {
                        if let Some(x) = get_path(&it.json, &f) {
                            set_path(&mut b, &f, x.clone());
                        }
                    }
                    b
                }
                _ => json!({}),
            };
            match v {
                Value::Object(o) if dest.is_empty() => {
                    for (k, x) in o {
                        base[k] = x;
                    }
                }
                v => set_path(&mut base, if dest.is_empty() { field } else { &dest }, v),
            }
            out.push(Item::paired(base, i));
        }
    }
    Ok(vec![out])
}

fn aggregate(ctx: &Ctx) -> Result<Out> {
    let items = ctx.items();
    if ctx.s("aggregate", 0, "aggregateIndividualFields")? == "aggregateAllItemData" {
        let dest = ctx.s("destinationFieldName", 0, "data")?;
        let include = ctx.s("include", 0, "allFields")?;
        let sel = fields(&ctx.s("fieldsToInclude", 0, "")?);
        let exc = fields(&ctx.s("fieldsToExclude", 0, "")?);
        let data: Vec<Value> = items
            .iter()
            .map(|it| match include.as_str() {
                "specifiedFields" => Value::Object(sel.iter().filter_map(|f| get_path(&it.json, f).map(|v| (f.clone(), v.clone()))).collect()),
                "allFieldsExcept" => {
                    let mut j = it.json.clone();
                    exc.iter().for_each(|f| remove_path(&mut j, f));
                    j
                }
                _ => it.json.clone(),
            })
            .collect();
        return Ok(vec![vec![Item::new(json!({ dest: data }))]]);
    }
    let merge_lists = ctx.b("options.mergeLists", 0, false)?;
    let keep_missing = ctx.b("options.keepMissing", 0, false)?;
    let mut o = json!({});
    for f in ctx.list("fieldsToAggregate.fieldToAggregate", 0)? {
        let name = as_str(&f["fieldToAggregate"]);
        let out_name = if f["renameField"].as_bool() == Some(true) { as_str(&f["outputFieldName"]) } else { name.clone() };
        let mut vals = vec![];
        for it in items {
            match get_path(&it.json, &name) {
                Some(Value::Array(a)) if merge_lists => vals.extend(a.clone()),
                Some(v) if !v.is_null() || keep_missing => vals.push(v.clone()),
                None if keep_missing => vals.push(Value::Null),
                _ => {}
            }
        }
        set_path(&mut o, &out_name, Value::Array(vals));
    }
    Ok(vec![vec![Item::new(o)]])
}

pub fn cmp_values(a: &Value, b: &Value) -> Ordering {
    match (a, b) {
        (Value::Null, Value::Null) => Ordering::Equal,
        (Value::Null, _) => Ordering::Greater,
        (_, Value::Null) => Ordering::Less,
        _ => match (as_f64(a), as_f64(b)) {
            (Some(x), Some(y)) if !(a.is_string() && b.is_string()) => x.partial_cmp(&y).unwrap_or(Ordering::Equal),
            _ => as_str(a).to_lowercase().cmp(&as_str(b).to_lowercase()),
        },
    }
}

fn sort(ctx: &Ctx) -> Result<Out> {
    let mut items = ctx.input(0);
    match ctx.s("type", 0, "simple")?.as_str() {
        "random" => {
            // Fisher-Yates with uuid randomness (avoids a rand dependency).
            for i in (1..items.len()).rev() {
                let r = uuid::Uuid::new_v4().as_u128() as usize % (i + 1);
                items.swap(i, r);
            }
        }
        _ => {
            let keys: Vec<(String, bool)> = ctx
                .list("sortFieldsUi.sortField", 0)?
                .iter()
                .map(|f| (as_str(&f["fieldName"]), f["order"] == "descending"))
                .collect();
            items.sort_by(|a, b| {
                for (k, desc) in &keys {
                    let o = cmp_values(get_path(&a.json, k).unwrap_or(&Value::Null), get_path(&b.json, k).unwrap_or(&Value::Null));
                    if o != Ordering::Equal {
                        return if *desc { o.reverse() } else { o };
                    }
                }
                Ordering::Equal
            });
        }
    }
    Ok(vec![items])
}

fn limit(ctx: &Ctx) -> Result<Out> {
    let n = ctx.n("maxItems", 0, 1.0)? as usize;
    let items = ctx.input(0);
    Ok(vec![if ctx.s("keep", 0, "firstItems")? == "lastItems" {
        items[items.len().saturating_sub(n)..].to_vec()
    } else {
        items.into_iter().take(n).collect()
    }])
}

fn remove_duplicates(ctx: &Ctx) -> Result<Out> {
    let compare = ctx.s("compare", 0, "allFields")?;
    let mut seen = std::collections::HashSet::new();
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let key = match compare.as_str() {
            "selectedFields" => {
                let f = fields(&ctx.s("fieldsToCompare", i, "")?);
                Value::Array(f.iter().map(|x| get_path(&it.json, x).cloned().unwrap_or(Value::Null)).collect())
            }
            "allFieldsExcept" => {
                let mut j = it.json.clone();
                fields(&ctx.s("fieldsToExclude", i, "")?).iter().for_each(|f| remove_path(&mut j, f));
                j
            }
            _ => it.json.clone(),
        };
        if seen.insert(key.to_string()) {
            out.push(it.clone());
        }
    }
    Ok(vec![out])
}

fn summarize(ctx: &Ctx) -> Result<Out> {
    let split_by = fields(&ctx.s("fieldsToSplitBy", 0, "")?);
    let aggs = ctx.list("fieldsToSummarize.values", 0)?;
    let aggs = if aggs.is_empty() { vec![json!({"aggregation": "count", "field": ""})] } else { aggs };
    let mut groups: Vec<(Value, Vec<&Item>)> = vec![];
    for it in ctx.items() {
        let key = Value::Array(split_by.iter().map(|f| get_path(&it.json, f).cloned().unwrap_or(Value::Null)).collect());
        match groups.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => v.push(it),
            None => groups.push((key, vec![it])),
        }
    }
    let mut out = vec![];
    for (key, members) in groups {
        let mut o = Map::new();
        for (f, v) in split_by.iter().zip(key.as_array().unwrap()) {
            o.insert(f.clone(), v.clone());
        }
        for a in &aggs {
            let agg = a["aggregation"].as_str().unwrap_or("count");
            let field = as_str(&a["field"]);
            let vals: Vec<Value> = members.iter().filter_map(|m| get_path(&m.json, &field).cloned()).filter(|v| !v.is_null()).collect();
            let nums: Vec<f64> = vals.iter().filter_map(as_f64).collect();
            let (name, v) = match agg {
                "append" => (format!("appended_{field}"), Value::Array(vals.clone())),
                "average" => (format!("average_{field}"), json!(if nums.is_empty() { 0.0 } else { nums.iter().sum::<f64>() / nums.len() as f64 })),
                "concatenate" => {
                    let sep = match a["separateBy"].as_str().unwrap_or(",") {
                        "other" => as_str(&a["customSeparator"]),
                        "\\n" | "newLine" => "\n".into(),
                        s => s.to_string(),
                    };
                    (format!("concatenated_{field}"), json!(vals.iter().map(as_str).collect::<Vec<_>>().join(&sep)))
                }
                "countUnique" => {
                    let set: std::collections::HashSet<String> = vals.iter().map(|v| v.to_string()).collect();
                    (format!("unique_count_{field}"), json!(set.len()))
                }
                "max" => (format!("max_{field}"), json!(nums.iter().cloned().fold(f64::NEG_INFINITY, f64::max))),
                "min" => (format!("min_{field}"), json!(nums.iter().cloned().fold(f64::INFINITY, f64::min))),
                "sum" => (format!("sum_{field}"), num(nums.iter().sum())),
                _ => (format!("count_{field}"), json!(if field.is_empty() { members.len() } else { vals.len() })),
            };
            o.insert(name, v);
        }
        out.push(Item::new(Value::Object(o)));
    }
    Ok(vec![out])
}

fn num(f: f64) -> Value {
    if f.fract() == 0.0 && f.abs() < 9e15 { json!(f as i64) } else { json!(f) }
}

fn rename_keys(ctx: &Ctx) -> Result<Out> {
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let mut j = it.json.clone();
        for k in ctx.list("keys.key", i)? {
            let (from, to) = (as_str(&k["currentKey"]), as_str(&k["newKey"]));
            if let Some(v) = get_path(&j, &from).cloned() {
                remove_path(&mut j, &from);
                set_path(&mut j, &to, v);
            }
        }
        let mut it = it.clone();
        it.json = j;
        out.push(it);
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

/// Parses ISO 8601, RFC 2822, SQL style and plain dates, plus epoch seconds/millis.
pub fn parse_date(s: &str) -> Option<DateTime<FixedOffset>> {
    let s = s.trim();
    if let Ok(d) = DateTime::parse_from_rfc3339(s) {
        return Some(d);
    }
    if let Ok(d) = DateTime::parse_from_rfc2822(s) {
        return Some(d);
    }
    let utc = |n: NaiveDateTime| Some(Utc.from_utc_datetime(&n).fixed_offset());
    for f in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"] {
        if let Ok(n) = NaiveDateTime::parse_from_str(s, f) {
            return utc(n);
        }
    }
    for f in ["%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y"] {
        if let Ok(d) = NaiveDate::parse_from_str(s, f) {
            return utc(d.and_hms_opt(0, 0, 0)?);
        }
    }
    let n: i64 = s.parse().ok()?;
    let ms = if n.abs() < 100_000_000_000 { n * 1000 } else { n };
    Some(Utc.timestamp_millis_opt(ms).single()?.fixed_offset())
}

fn date_of(v: &Value) -> Result<DateTime<FixedOffset>> {
    match v {
        Value::Number(n) => parse_date(&n.to_string()),
        Value::String(s) => parse_date(s),
        _ => None,
    }
    .ok_or_else(|| anyhow!("Invalid date: {}", as_str(v)))
}

/// Converts a Luxon format string (as used by n8n) to chrono's strftime syntax.
pub fn luxon_to_chrono(f: &str) -> String {
    let tokens = [
        ("yyyy", "%Y"), ("yy", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("M", "%-m"), ("dd", "%d"), ("d", "%-d"),
        ("HH", "%H"), ("H", "%-H"), ("hh", "%I"), ("h", "%-I"), ("mm", "%M"), ("m", "%-M"), ("ss", "%S"), ("s", "%-S"),
        ("SSS", "%3f"), ("a", "%p"), ("EEEE", "%A"), ("EEE", "%a"), ("ZZ", "%:z"), ("Z", "%z"), ("X", "%s"),
    ];
    let mut out = String::new();
    let mut rest = f;
    'outer: while !rest.is_empty() {
        if let Some(r) = rest.strip_prefix('\'') {
            let end = r.find('\'').unwrap_or(r.len());
            out.push_str(&r[..end].replace('%', "%%"));
            rest = r.get(end + 1..).unwrap_or("");
            continue;
        }
        for (t, c) in tokens {
            if let Some(r) = rest.strip_prefix(t) {
                out.push_str(c);
                rest = r;
                continue 'outer;
            }
        }
        let ch = rest.chars().next().unwrap();
        if ch == '%' { out.push_str("%%") } else { out.push(ch) }
        rest = &rest[ch.len_utf8()..];
    }
    out
}

fn in_tz(d: DateTime<FixedOffset>, tz: &str) -> String {
    match tz.parse::<chrono_tz::Tz>() {
        Ok(z) => d.with_timezone(&z).to_rfc3339_opts(chrono::SecondsFormat::Millis, false),
        Err(_) => d.to_rfc3339_opts(chrono::SecondsFormat::Millis, false),
    }
}

fn shift(d: DateTime<FixedOffset>, unit: &str, n: i64) -> Result<DateTime<FixedOffset>> {
    use chrono::{Duration, Months};
    let months = |m: i64| if m >= 0 { d.checked_add_months(Months::new(m as u32)) } else { d.checked_sub_months(Months::new((-m) as u32)) };
    Ok(match unit.trim_end_matches('s') {
        "year" => months(n * 12),
        "quarter" => months(n * 3),
        "month" => months(n),
        "week" => Some(d + Duration::weeks(n)),
        "day" => Some(d + Duration::days(n)),
        "hour" => Some(d + Duration::hours(n)),
        "minute" => Some(d + Duration::minutes(n)),
        "second" => Some(d + Duration::seconds(n)),
        "millisecond" => Some(d + Duration::milliseconds(n)),
        u => return Err(anyhow!("Unknown time unit: {u}")),
    }
    .ok_or_else(|| anyhow!("Date out of range"))?)
}

fn date_time(ctx: &Ctx) -> Result<Out> {
    let op = ctx.s("operation", 0, "getCurrentDate")?;
    let tz = ctx.s("options.timezone", 0, &ctx.eng.wf.setting("timezone").map(as_str).filter(|s| !s.is_empty() && s != "DEFAULT").unwrap_or(ctx.eng.app.cfg.timezone.clone()))?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let (field, v) = match op.as_str() {
            "getCurrentDate" => {
                let now = Utc::now().fixed_offset();
                let v = if ctx.b("includeTime", i, true)? { in_tz(now, &tz) } else { in_tz(now, &tz)[..10].to_string() };
                (ctx.s("outputFieldName", i, "currentDate")?, json!(v))
            }
            "addToDate" | "subtractFromDate" => {
                let d = date_of(&ctx.val("magnitude", i, Value::Null)?)?;
                let n = ctx.n("duration", i, 0.0)? as i64;
                let n = if op == "subtractFromDate" { -n } else { n };
                (ctx.s("outputFieldName", i, "newDate")?, json!(in_tz(shift(d, &ctx.s("timeUnit", i, "days")?, n)?, &tz)))
            }
            "formatDate" => {
                let d = date_of(&ctx.val("date", i, Value::Null)?)?;
                let fmt = match ctx.s("format", i, "MM/dd/yyyy")?.as_str() {
                    "custom" => ctx.s("customFormat", i, "yyyy-MM-dd")?,
                    f => f.to_string(),
                };
                let s = match tz.parse::<chrono_tz::Tz>() {
                    Ok(z) => d.with_timezone(&z).format(&luxon_to_chrono(&fmt)).to_string(),
                    Err(_) => d.format(&luxon_to_chrono(&fmt)).to_string(),
                };
                (ctx.s("outputFieldName", i, "formattedDate")?, json!(s))
            }
            "getTimeBetweenDates" => {
                let a = date_of(&ctx.val("startDate", i, Value::Null)?)?;
                let b = date_of(&ctx.val("endDate", i, Value::Null)?)?;
                let ms = (b - a).num_milliseconds() as f64;
                let units = match ctx.val("units", i, json!(["day"]))? {
                    Value::Array(a) => a.iter().map(as_str).collect(),
                    v => vec![as_str(&v)],
                };
                let mut o = Map::new();
                for u in units {
                    let f = match u.as_str() {
                        "year" => 31_536_000_000.0, "month" => 2_592_000_000.0, "week" => 604_800_000.0, "day" => 86_400_000.0,
                        "hour" => 3_600_000.0, "minute" => 60_000.0, "second" => 1000.0, _ => 1.0,
                    };
                    o.insert(u, num((ms / f).trunc()));
                }
                (ctx.s("outputFieldName", i, "timeDifference")?, Value::Object(o))
            }
            "extractDate" => {
                let d = date_of(&ctx.val("date", i, Value::Null)?)?;
                let p = ctx.s("part", i, "month")?;
                let v = match p.as_str() {
                    "year" => d.year() as u32, "month" => d.month(), "week" => d.iso_week().week(), "day" => d.day(),
                    "hour" => d.hour(), "minute" => d.minute(), "second" => d.second(), _ => 0,
                };
                (ctx.s("outputFieldName", i, "datePart")?, json!(v))
            }
            "roundDate" => {
                let d = date_of(&ctx.val("date", i, Value::Null)?)?;
                let unit = ctx.s("toNearest", i, "day")?;
                let down = trunc(d, &unit);
                let v = if ctx.s("mode", i, "roundDown")? == "roundUp" && down != d { shift(down, &unit, 1)? } else { down };
                (ctx.s("outputFieldName", i, "roundedDate")?, json!(in_tz(v, &tz)))
            }
            o => return Err(anyhow!("Operation \"{o}\" is not supported")),
        };
        let mut j = it.json.clone();
        set_path(&mut j, &field, v);
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

fn trunc(d: DateTime<FixedOffset>, unit: &str) -> DateTime<FixedOffset> {
    let date = match unit {
        "year" => d.with_month(1).and_then(|x| x.with_day(1)),
        "month" => d.with_day(1),
        "week" => Some(d - chrono::Duration::days(d.weekday().num_days_from_monday() as i64)),
        _ => Some(d),
    }
    .unwrap_or(d);
    let t = match unit {
        "hour" => date.with_minute(0).and_then(|x| x.with_second(0)),
        "minute" => date.with_second(0),
        "second" => Some(date),
        _ => date.with_hour(0).and_then(|x| x.with_minute(0)).and_then(|x| x.with_second(0)),
    }
    .unwrap_or(date);
    t.with_nanosecond(0).unwrap_or(t)
}

// ---------------------------------------------------------------------------
// Crypto
// ---------------------------------------------------------------------------

fn digest(alg: &str, data: &[u8]) -> Result<Vec<u8>> {
    use sha2::Digest;
    Ok(match alg.to_uppercase().as_str() {
        "MD5" => md5::Md5::digest(data).to_vec(),
        "SHA1" => sha1::Sha1::digest(data).to_vec(),
        "SHA256" => sha2::Sha256::digest(data).to_vec(),
        "SHA384" => sha2::Sha384::digest(data).to_vec(),
        "SHA512" => sha2::Sha512::digest(data).to_vec(),
        a => return Err(anyhow!("Unsupported algorithm: {a}")),
    })
}

fn hmac_of<M: hmac::Mac + hmac::digest::KeyInit>(key: &[u8], data: &[u8]) -> Vec<u8> {
    let mut m = <M as hmac::Mac>::new_from_slice(key).expect("hmac accepts any key length");
    m.update(data);
    m.finalize().into_bytes().to_vec()
}

pub fn hmac(alg: &str, key: &[u8], data: &[u8]) -> Result<Vec<u8>> {
    use hmac::Hmac;
    Ok(match alg.to_uppercase().as_str() {
        "MD5" => hmac_of::<Hmac<md5::Md5>>(key, data),
        "SHA1" => hmac_of::<Hmac<sha1::Sha1>>(key, data),
        "SHA256" => hmac_of::<Hmac<sha2::Sha256>>(key, data),
        "SHA384" => hmac_of::<Hmac<sha2::Sha384>>(key, data),
        "SHA512" => hmac_of::<Hmac<sha2::Sha512>>(key, data),
        a => return Err(anyhow!("Unsupported algorithm: {a}")),
    })
}

fn crypto(ctx: &Ctx) -> Result<Out> {
    let action = ctx.s("action", 0, "hash")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let prop = ctx.s("dataPropertyName", i, "data")?;
        let enc = |b: Vec<u8>| -> Result<String> { Ok(if ctx.s("encoding", i, "hex")? == "base64" { B64.encode(b) } else { hex::encode(b) }) };
        let v = match action.as_str() {
            "hash" => enc(digest(&ctx.s("type", i, "MD5")?, ctx.s("value", i, "")?.as_bytes())?)?,
            "hmac" => enc(hmac(&ctx.s("type", i, "MD5")?, ctx.s("secret", i, "")?.as_bytes(), ctx.s("value", i, "")?.as_bytes())?)?,
            "generate" => {
                let len = ctx.n("stringLength", i, 32.0)? as usize;
                let rnd = || {
                    let mut b = vec![];
                    while b.len() < len {
                        b.extend_from_slice(uuid::Uuid::new_v4().as_bytes());
                    }
                    b.truncate(len);
                    b
                };
                match ctx.s("encodingType", i, "uuid")?.as_str() {
                    "hex" => hex::encode(rnd())[..len].to_string(),
                    "base64" => B64.encode(rnd())[..len].to_string(),
                    "ascii" => rnd().iter().map(|b| (b'!' + b % 94) as char).collect(),
                    _ => uuid::Uuid::new_v4().to_string(),
                }
            }
            a => return Err(anyhow!("Action \"{a}\" is not supported")),
        };
        let mut j = it.json.clone();
        set_path(&mut j, &prop, json!(v));
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// HTML
// ---------------------------------------------------------------------------

fn html(ctx: &Ctx) -> Result<Out> {
    use scraper::{Html, Selector};
    let op = ctx.s("operation", 0, "extractHtmlContent")?;
    if op != "extractHtmlContent" {
        return Err(anyhow!("Operation \"{op}\" is not supported"));
    }
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let prop = ctx.s("dataPropertyName", i, "data")?;
        let src = if ctx.s("sourceData", i, "json")? == "binary" {
            let bin = it.binary.as_ref().and_then(|b| b.get(&prop)).ok_or_else(|| anyhow!("No binary property \"{prop}\""))?;
            String::from_utf8_lossy(&binary_bytes(bin)?).into_owned()
        } else {
            get_path(&it.json, &prop).map(as_str).unwrap_or_default()
        };
        let trim = ctx.b("options.trimValues", i, true)?;
        let doc = Html::parse_document(&src);
        let mut o = Map::new();
        for e in ctx.list("extractionValues.values", i)? {
            let sel = Selector::parse(&as_str(&e["cssSelector"])).map_err(|e| anyhow!("Invalid CSS selector: {e}"))?;
            let ret = e["returnValue"].as_str().unwrap_or("text");
            let vals: Vec<Value> = doc
                .select(&sel)
                .map(|el| {
                    let s = match ret {
                        "html" => el.inner_html(),
                        "attribute" => el.value().attr(e["attribute"].as_str().unwrap_or("")).unwrap_or("").to_string(),
                        "value" => el.value().attr("value").unwrap_or("").to_string(),
                        _ => el.text().collect::<String>(),
                    };
                    json!(if trim { s.trim().to_string() } else { s })
                })
                .collect();
            let v = if e["returnArray"].as_bool() == Some(true) { Value::Array(vals) } else { vals.into_iter().next().unwrap_or(Value::Null) };
            o.insert(as_str(&e["key"]), v);
        }
        out.push(Item::paired(Value::Object(o), i));
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Files / binary data
// ---------------------------------------------------------------------------

pub fn binary_bytes(bin: &Value) -> Result<Vec<u8>> {
    Ok(B64.decode(bin["data"].as_str().unwrap_or(""))?)
}

pub fn make_binary(bytes: &[u8], mime: &str, file_name: &str) -> Value {
    let ext = file_name.rsplit_once('.').map(|(_, e)| e.to_string()).unwrap_or_default();
    let size = bytes.len() as f64;
    let human = if size > 1e6 { format!("{:.1} MB", size / 1e6) } else if size > 1e3 { format!("{:.1} kB", size / 1e3) } else { format!("{size} B") };
    json!({"data": B64.encode(bytes), "mimeType": mime, "fileName": file_name, "fileExtension": ext, "fileSize": human})
}

pub fn mime_for(name: &str) -> &'static str {
    match name.rsplit('.').next().unwrap_or("").to_lowercase().as_str() {
        "json" => "application/json", "csv" => "text/csv", "txt" => "text/plain", "html" | "htm" => "text/html",
        "xml" => "application/xml", "pdf" => "application/pdf", "png" => "image/png", "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif", "svg" => "image/svg+xml", "webp" => "image/webp", "zip" => "application/zip",
        "js" | "mjs" => "text/javascript; charset=utf-8", "css" => "text/css; charset=utf-8", "ico" => "image/x-icon",
        "woff2" => "font/woff2", "webmanifest" => "application/manifest+json",
        _ => "application/octet-stream",
    }
}

fn extract_from_file(ctx: &Ctx) -> Result<Out> {
    let op = ctx.s("operation", 0, "csv")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let prop = ctx.s("binaryPropertyName", i, "data")?;
        let bin = it.binary.as_ref().and_then(|b| b.get(&prop)).ok_or_else(|| anyhow!("This operation expects the node's input data to contain a binary file '{prop}', but none was found [item {i}]"))?;
        let bytes = binary_bytes(bin)?;
        match op.as_str() {
            "csv" => {
                let delim = ctx.s("options.delimiter", i, ",")?;
                let header = ctx.b("options.headerRow", i, true)?;
                let mut r = csv::ReaderBuilder::new()
                    .delimiter(*delim.as_bytes().first().unwrap_or(&b','))
                    .has_headers(header)
                    .flexible(true)
                    .from_reader(bytes.as_slice());
                let heads: Vec<String> = if header { r.headers()?.iter().map(String::from).collect() } else { vec![] };
                for rec in r.records() {
                    let rec = rec?;
                    let o: Map<String, Value> = rec
                        .iter()
                        .enumerate()
                        .map(|(c, v)| (heads.get(c).cloned().unwrap_or_else(|| format!("field{c}")), json!(v)))
                        .collect();
                    out.push(Item::paired(Value::Object(o), i));
                }
            }
            "fromJson" => {
                let v: Value = serde_json::from_slice(&bytes)?;
                let dest = ctx.s("destinationKey", i, "data")?;
                out.push(Item::paired(json!({ dest: v }), i));
            }
            "text" | "html" | "xml" => {
                let dest = ctx.s("destinationKey", i, "data")?;
                out.push(Item::paired(json!({ dest: String::from_utf8_lossy(&bytes) }), i));
            }
            "binaryToPropery" | "binaryToProperty" => {
                let dest = ctx.s("destinationKey", i, "data")?;
                out.push(Item::paired(json!({ dest: B64.encode(&bytes) }), i));
            }
            o => return Err(anyhow!("Operation \"{o}\" is not supported")),
        }
    }
    Ok(vec![out])
}

fn convert_to_file(ctx: &Ctx) -> Result<Out> {
    let op = ctx.s("operation", 0, "csv")?;
    let prop = ctx.s("binaryPropertyName", 0, "data")?;
    let one = |bytes: Vec<u8>, default_name: &str| -> Result<Item> {
        let name = ctx.s("options.fileName", 0, default_name)?;
        let mut m = Map::new();
        m.insert(prop.clone(), make_binary(&bytes, mime_for(&name), &name));
        Ok(Item { json: json!({}), binary: Some(m), paired_item: None })
    };
    let items = ctx.items();
    Ok(vec![match op.as_str() {
        "csv" => {
            let delim = ctx.s("options.delimiter", 0, ",")?;
            let mut heads: Vec<String> = vec![];
            for it in items {
                for k in it.json.as_object().into_iter().flat_map(|o| o.keys()) {
                    if !heads.contains(k) {
                        heads.push(k.clone());
                    }
                }
            }
            let mut w = csv::WriterBuilder::new().delimiter(*delim.as_bytes().first().unwrap_or(&b',')).from_writer(vec![]);
            if ctx.b("options.headerRow", 0, true)? {
                w.write_record(&heads)?;
            }
            for it in items {
                w.write_record(heads.iter().map(|h| it.json.get(h).map(as_str).unwrap_or_default()))?;
            }
            vec![one(w.into_inner().map_err(|e| anyhow!("{e}"))?, "data.csv")?]
        }
        "toJson" => {
            let pretty = ctx.b("options.format", 0, false)?;
            let ser = |v: &Value| if pretty { serde_json::to_vec_pretty(v) } else { serde_json::to_vec(v) };
            if ctx.s("mode", 0, "allItems")? == "each" {
                items.iter().map(|it| one(ser(&it.json)?, "data.json")).collect::<Result<_>>()?
            } else {
                vec![one(ser(&Value::Array(items.iter().map(|i| i.json.clone()).collect()))?, "data.json")?]
            }
        }
        "toText" => {
            let src = ctx.s("sourceProperty", 0, "data")?;
            items.iter().map(|it| one(get_path(&it.json, &src).map(as_str).unwrap_or_default().into_bytes(), "data.txt")).collect::<Result<_>>()?
        }
        "iCal" => {
            let mut files = vec![];
            for i in 0..items.len() {
                let fmt = |p: &str| -> Result<String> {
                    let d = parse_date(&ctx.s(p, i, "")?).ok_or_else(|| anyhow!("'{p}' is not a valid date"))?;
                    Ok(d.with_timezone(&Utc).format("%Y%m%dT%H%M%SZ").to_string())
                };
                let esc = |s: String| s.replace('\\', "\\\\").replace(';', "\\;").replace(',', "\\,").replace('\n', "\\n");
                let mut ev = vec![
                    "BEGIN:VCALENDAR".to_string(), "VERSION:2.0".into(), "PRODID:-//Flowwolf//EN".into(), "BEGIN:VEVENT".into(),
                    format!("UID:{}@flowwolf", uuid::Uuid::new_v4()),
                    format!("DTSTAMP:{}", Utc::now().format("%Y%m%dT%H%M%SZ")),
                ];
                if ctx.b("allDay", i, false)? {
                    let day = |p: &str| -> Result<String> { Ok(parse_date(&ctx.s(p, i, "")?).ok_or_else(|| anyhow!("'{p}' is not a valid date"))?.format("%Y%m%d").to_string()) };
                    ev.push(format!("DTSTART;VALUE=DATE:{}", day("start")?));
                    ev.push(format!("DTEND;VALUE=DATE:{}", day("end")?));
                } else {
                    ev.push(format!("DTSTART:{}", fmt("start")?));
                    ev.push(format!("DTEND:{}", fmt("end")?));
                }
                ev.push(format!("SUMMARY:{}", esc(ctx.s("title", i, "")?)));
                for (field, key) in [("additionalFields.description", "DESCRIPTION"), ("additionalFields.location", "LOCATION"), ("additionalFields.url", "URL")] {
                    let v = ctx.s(field, i, "")?;
                    if !v.is_empty() {
                        ev.push(format!("{key}:{}", esc(v)));
                    }
                }
                ev.extend(["END:VEVENT".to_string(), "END:VCALENDAR".into()]);
                let name = ctx.s("options.fileName", 0, "event.ics")?;
                let mut m = Map::new();
                m.insert(prop.clone(), make_binary((ev.join("\r\n") + "\r\n").as_bytes(), "text/calendar", &name));
                files.push(Item { json: json!({}), binary: Some(m), paired_item: Some(json!({"item": i})) });
            }
            files
        }
        "toBinary" => {
            let src = ctx.s("sourceProperty", 0, "data")?;
            items.iter().map(|it| one(B64.decode(get_path(&it.json, &src).map(as_str).unwrap_or_default())?, "file")).collect::<Result<_>>()?
        }
        o => return Err(anyhow!("Operation \"{o}\" is not supported")),
    }])
}

/// Resolves `p` inside the allowed files directory, rejecting escapes via `..` or symlinks.
fn safe_path(root: &str, p: &str) -> Result<std::path::PathBuf> {
    std::fs::create_dir_all(root)?;
    let root = std::fs::canonicalize(root)?;
    let joined = if std::path::Path::new(p).is_absolute() { std::path::PathBuf::from(p) } else { root.join(p) };
    let parent = joined.parent().ok_or_else(|| anyhow!("Invalid path"))?;
    std::fs::create_dir_all(parent).ok();
    let canon_parent = std::fs::canonicalize(parent)?;
    if !canon_parent.starts_with(&root) {
        return Err(anyhow!("Access to '{p}' is not allowed. Files must be inside {}", root.display()));
    }
    Ok(canon_parent.join(joined.file_name().ok_or_else(|| anyhow!("Invalid path"))?))
}

async fn read_write_file(ctx: &Ctx<'_>) -> Result<Out> {
    let root = ctx.eng.app.cfg.files_dir.clone();
    let op = ctx.s("operation", 0, "read")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        if op == "read" {
            let p = ctx.s("fileSelector", i, "")?;
            let path = safe_path(&root, &p)?;
            let bytes = tokio::fs::read(&path).await.map_err(|e| anyhow!("Cannot read '{p}': {e}"))?;
            let name = path.file_name().unwrap().to_string_lossy().to_string();
            let prop = ctx.s("options.dataPropertyName", i, "data")?;
            let mut m = Map::new();
            m.insert(prop, make_binary(&bytes, mime_for(&name), &name));
            out.push(Item { json: json!({"fileName": name}), binary: Some(m), paired_item: Some(json!({"item": i})) });
        } else {
            let p = ctx.s("fileName", i, "")?;
            let prop = ctx.s("dataPropertyName", i, "data")?;
            let bin = it.binary.as_ref().and_then(|b| b.get(&prop)).ok_or_else(|| anyhow!("No binary property \"{prop}\" [item {i}]"))?;
            let path = safe_path(&root, &p)?;
            let bytes = binary_bytes(bin)?;
            if ctx.b("options.append", i, false)? {
                use tokio::io::AsyncWriteExt;
                let mut f = tokio::fs::OpenOptions::new().create(true).append(true).open(&path).await?;
                f.write_all(&bytes).await?;
            } else {
                tokio::fs::write(&path, &bytes).await?;
            }
            let mut it = it.clone();
            it.json["fileName"] = json!(p);
            out.push(it);
        }
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// RSS / Atom
// ---------------------------------------------------------------------------

fn xml_text(s: &str) -> String {
    let s = s.trim();
    let s = s.strip_prefix("<![CDATA[").and_then(|x| x.strip_suffix("]]>")).unwrap_or(s);
    s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'").replace("&apos;", "'").replace("&amp;", "&")
}

/// Parses RSS 2.0 `<item>` and Atom `<entry>` elements into flat objects.
pub fn parse_feed(xml: &str) -> Vec<Value> {
    // ponytail: regex extraction instead of a full XML parser; enough for real-world feeds.
    let block = regex::Regex::new(r"(?s)<(item|entry)[\s>](.*?)</(?:item|entry)>").unwrap();
    let open = regex::Regex::new(r"<([A-Za-z][\w:.-]*)(\s[^>]*?)?(/?)>").unwrap();
    let href = regex::Regex::new(r#"href\s*=\s*"([^"]*)""#).unwrap();
    block
        .captures_iter(xml)
        .map(|b| {
            let body = &b[2];
            let mut o = Map::new();
            let mut pos = 0;
            while let Some(c) = open.captures_at(body, pos) {
                let m = c.get(0).unwrap();
                let tag = c[1].to_string();
                let attrs = c.get(2).map(|x| x.as_str()).unwrap_or("");
                let (text, next) = if &c[3] == "/" {
                    (String::new(), m.end())
                } else {
                    let close = format!("</{tag}>");
                    match body[m.end()..].find(&close) {
                        Some(e) => (xml_text(&body[m.end()..m.end() + e]), m.end() + e + close.len()),
                        None => (String::new(), m.end()),
                    }
                };
                pos = next;
                let name = tag.replace("content:encoded", "content").replace("dc:creator", "creator");
                let val = if name == "link" && text.is_empty() { href.captures(attrs).map(|h| h[1].to_string()).unwrap_or_default() } else { text };
                match o.get_mut(&name) {
                    Some(Value::Array(a)) => a.push(json!(val)),
                    Some(prev) if name == "category" => *prev = json!([prev.clone(), val]),
                    Some(_) => {}
                    None => {
                        o.insert(name, json!(val));
                    }
                }
            }
            if let Some(d) = o.get("pubDate").or(o.get("published")).or(o.get("updated")).map(as_str).and_then(|d| parse_date(&d)) {
                o.insert("isoDate".into(), json!(d.to_rfc3339_opts(chrono::SecondsFormat::Millis, true)));
            }
            Value::Object(o)
        })
        .collect()
}

async fn rss(ctx: &Ctx<'_>) -> Result<Out> {
    let mut out = vec![];
    for i in 0..ctx.items().len().max(1) {
        let url = ctx.s("url", i, "")?;
        let text = ctx.eng.app.http.get(&url).send().await?.error_for_status()?.text().await?;
        out.extend(parse_feed(&text).into_iter().map(|v| Item::paired(v, i)));
        if ctx.node.execute_once || ctx.items().len() <= 1 {
            break;
        }
    }
    Ok(vec![out])
}

#[cfg(test)]
mod tests {
    #[test]
    fn feeds() {
        let rss = r#"<rss><channel><item><title>A &amp; B</title><link>https://x/1</link><pubDate>Tue, 05 Mar 2024 10:00:00 GMT</pubDate><description><![CDATA[<p>hi</p>]]></description></item></channel></rss>"#;
        let v = super::parse_feed(rss);
        assert_eq!(v[0]["title"], serde_json::json!("A & B"));
        assert_eq!(v[0]["description"], serde_json::json!("<p>hi</p>"));
        assert!(v[0]["isoDate"].as_str().unwrap().starts_with("2024-03-05"));
        let atom = r#"<feed><entry><title>T</title><link href="https://y/2"/><updated>2024-01-01T00:00:00Z</updated></entry></feed>"#;
        assert_eq!(super::parse_feed(atom)[0]["link"], serde_json::json!("https://y/2"));
    }
}

#[cfg(test)]
mod tests_dates {
    use super::*;

    #[test]
    fn dates_and_hashes() {
        assert_eq!(luxon_to_chrono("yyyy-MM-dd 'at' HH:mm"), "%Y-%m-%d at %H:%M");
        assert_eq!(parse_date("2024-03-05").unwrap().day(), 5);
        assert_eq!(parse_date("1700000000").unwrap().year(), 2023);
        let d = parse_date("2024-01-31T10:00:00Z").unwrap();
        assert_eq!(shift(d, "months", 1).unwrap().day(), 29);
        assert_eq!(hex::encode(digest("md5", b"abc").unwrap()), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(hex::encode(hmac("sha256", b"key", b"msg").unwrap()).len(), 64);
        assert_eq!(trunc(d, "month").to_rfc3339(), "2024-01-01T00:00:00+00:00");
    }
}
