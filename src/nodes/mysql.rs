//! MySQL / MariaDB node. Values are always sent as prepared statement parameters.

use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use mysql_async::Value as V;
use mysql_async::prelude::*;
use serde_json::{Map, Value, json};

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    (kind == "n8n-nodes-base.mySql").then_some(())?;
    Some(node(ctx).await)
}

fn to_my(v: &Value) -> V {
    match v {
        Value::Null => V::NULL,
        Value::Bool(b) => V::Int(*b as i64),
        Value::Number(n) => n.as_i64().map(V::Int).or_else(|| n.as_u64().map(V::UInt)).unwrap_or_else(|| V::Double(n.as_f64().unwrap_or(0.0))),
        Value::String(s) => V::Bytes(s.clone().into_bytes()),
        other => V::Bytes(other.to_string().into_bytes()),
    }
}

fn from_my(v: V) -> Value {
    match v {
        V::NULL => Value::Null,
        V::Bytes(b) => match String::from_utf8(b) {
            Ok(s) => json!(s),
            Err(e) => json!(base64::engine::general_purpose::STANDARD.encode(e.into_bytes())),
        },
        V::Int(i) => json!(i),
        V::UInt(u) => json!(u),
        V::Float(f) => json!(f),
        V::Double(d) => json!(d),
        V::Date(y, mo, d, h, mi, s, us) => {
            if (h, mi, s, us) == (0, 0, 0, 0) {
                json!(format!("{y:04}-{mo:02}-{d:02}"))
            } else {
                json!(format!("{y:04}-{mo:02}-{d:02} {h:02}:{mi:02}:{s:02}"))
            }
        }
        V::Time(neg, d, h, mi, s, _) => json!(format!("{}{:02}:{mi:02}:{s:02}", if neg { "-" } else { "" }, d * 24 + h as u32)),
    }
}

fn ident(s: &str) -> String {
    format!("`{}`", s.replace('`', "``"))
}

async fn query(conn: &mut mysql_async::Conn, sql: &str, params: Vec<V>) -> Result<Vec<Item>> {
    let lower = sql.trim_start().to_lowercase();
    if ["select", "show", "describe", "desc ", "with", "explain", "call"].iter().any(|k| lower.starts_with(k)) {
        let rows: Vec<mysql_async::Row> = conn.exec(sql, params).await?;
        return Ok(rows
            .into_iter()
            .map(|r| {
                let cols: Vec<String> = r.columns_ref().iter().map(|c| c.name_str().into_owned()).collect();
                let vals = r.unwrap();
                Item::new(Value::Object(cols.into_iter().zip(vals.into_iter().map(from_my)).collect::<Map<_, _>>()))
            })
            .collect());
    }
    conn.exec_drop(sql, params).await?;
    Ok(vec![Item::new(json!({"success": true, "affectedRows": conn.affected_rows(), "insertId": conn.last_insert_id()}))])
}

async fn node(ctx: &Ctx<'_>) -> Result<Out> {
    let c = ctx.creds("mySql")?;
    let mut opts = mysql_async::OptsBuilder::default()
        .ip_or_hostname(as_str(&c["host"]))
        .tcp_port(as_f64(&c["port"]).unwrap_or(3306.0) as u16)
        .user(Some(as_str(&c["user"])))
        .pass(Some(as_str(&c["password"])))
        .db_name(Some(as_str(&c["database"])));
    if c["ssl"] == true {
        opts = opts.ssl_opts(Some(mysql_async::SslOpts::default().with_danger_accept_invalid_certs(c["allowUnauthorizedCerts"] == true)));
    }
    let mut conn = mysql_async::Conn::new(opts).await.map_err(|e| anyhow!("Could not connect to MySQL: {e}"))?;
    let op = ctx.s("operation", 0, "executeQuery")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let (sql, params): (String, Vec<V>) = match op.as_str() {
            "executeQuery" => {
                let q = ctx.s("query", i, "")?;
                let vals: Vec<Value> = match ctx.val("options.queryReplacement", i, json!(""))? {
                    Value::Array(a) => a,
                    Value::String(s) if !s.is_empty() => s.split(',').map(|x| json!(x.trim())).collect(),
                    Value::String(_) => vec![],
                    v => vec![v],
                };
                (q, vals.iter().map(to_my).collect())
            }
            "insert" | "upsert" => {
                let table = ctx.s("table", i, "")?;
                let obj = if ctx.s("dataMode", i, "autoMapInputData")? == "defineBelow" { ctx.val("valuesToSend", i, json!({}))? } else { it.json.clone() };
                let obj = match obj {
                    Value::String(s) => serde_json::from_str(&s)?,
                    v => v,
                };
                let o = obj.as_object().cloned().unwrap_or_default();
                if o.is_empty() {
                    return Err(anyhow!("Nothing to insert: the item has no fields"));
                }
                let cols: Vec<String> = o.keys().map(|k| ident(k)).collect();
                let mut sql = format!("INSERT INTO {} ({}) VALUES ({})", ident(&table), cols.join(", "), vec!["?"; cols.len()].join(", "));
                if op == "upsert" {
                    let set: Vec<String> = cols.iter().map(|c| format!("{c} = VALUES({c})")).collect();
                    sql.push_str(&format!(" ON DUPLICATE KEY UPDATE {}", set.join(", ")));
                }
                (sql, o.values().map(to_my).collect())
            }
            "select" => {
                let table = ctx.s("table", i, "")?;
                let limit = if ctx.b("returnAll", i, false)? { String::new() } else { format!(" LIMIT {}", ctx.n("limit", i, 50.0)? as i64) };
                (format!("SELECT * FROM {}{limit}", ident(&table)), vec![])
            }
            "deleteTable" => (format!("TRUNCATE TABLE {}", ident(&ctx.s("table", i, "")?)), vec![]),
            o => return Err(anyhow!("Operation \"{o}\" is not supported")),
        };
        for mut item in query(&mut conn, &sql, params).await.map_err(|e| anyhow!("MySQL error: {e}"))? {
            item.paired_item = Some(json!({"item": i}));
            out.push(item);
        }
        if (op == "executeQuery" && ctx.node.execute_once) || op == "select" || op == "deleteTable" {
            break;
        }
    }
    let _ = conn.disconnect().await;
    Ok(vec![out])
}
