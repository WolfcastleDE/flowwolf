//! MongoDB: find, aggregate, insert, update, find-and-modify, delete and Atlas search indexes.

use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use futures::TryStreamExt;
use mongodb::bson::{self, Bson, Document, doc, oid::ObjectId};
use serde_json::{Map, Value, json};

/// BSON to the JSON n8n produces: ObjectIds as hex strings, dates as ISO strings.
fn to_json(b: Bson) -> Value {
    match b {
        Bson::ObjectId(o) => json!(o.to_hex()),
        Bson::DateTime(d) => json!(d.try_to_rfc3339_string().unwrap_or_default()),
        Bson::Document(d) => Value::Object(d.into_iter().map(|(k, v)| (k, to_json(v))).collect()),
        Bson::Array(a) => Value::Array(a.into_iter().map(to_json).collect()),
        Bson::Decimal128(d) => json!(d.to_string()),
        other => other.into_relaxed_extjson(),
    }
}

fn to_bson(v: &Value) -> Result<Bson> {
    Bson::try_from(v.clone()).map_err(|e| anyhow!("Invalid value for MongoDB: {e}"))
}

fn to_doc(v: &Value, what: &str) -> Result<Document> {
    match to_bson(v)? {
        Bson::Document(d) => Ok(d),
        _ => Err(anyhow!("{what} must be a JSON object")),
    }
}

/// JSON parameters arrive as text or as objects.
fn json_param(v: Value, what: &str) -> Result<Value> {
    match v {
        Value::String(s) if s.trim().is_empty() => Ok(json!({})),
        Value::String(s) => serde_json::from_str(&s).map_err(|e| anyhow!("{what} must be valid JSON: {e}")),
        v => Ok(v),
    }
}

/// A 24-character hex `_id` in a filter means an ObjectId (as n8n treats it).
fn object_ids(d: &mut Document) {
    if let Some(Bson::String(s)) = d.get("_id") {
        if let Ok(o) = ObjectId::parse_str(s) {
            d.insert("_id", o);
        }
    }
}

/// The chosen fields of an item (all fields when none are named), with date fields converted
/// and dotted names expanded into nested objects when asked to.
fn item_fields(item: &Value, fields: &str, date_fields: &str, dot_notation: bool) -> Result<Document> {
    let names: Vec<&str> = fields.split(',').map(str::trim).filter(|f| !f.is_empty()).collect();
    let mut m = Map::new();
    match item {
        Value::Object(o) if names.is_empty() => m = o.clone(),
        _ => {
            for n in &names {
                let v = if dot_notation { item.pointer(&format!("/{}", n.replace('.', "/"))).cloned() } else { item.get(*n).cloned() };
                if let Some(v) = v {
                    m.insert(n.to_string(), v);
                }
            }
        }
    }
    let mut d = to_doc(&Value::Object(m), "Item")?;
    for f in date_fields.split(',').map(str::trim).filter(|f| !f.is_empty()) {
        if let Some(Bson::String(s)) = d.get(f) {
            if let Ok(t) = chrono::DateTime::parse_from_rfc3339(s) {
                d.insert(f, bson::DateTime::from_millis(t.timestamp_millis()));
            }
        }
    }
    Ok(d)
}

async fn connect(ctx: &Ctx<'_>) -> Result<(mongodb::Client, String)> {
    let c = ctx.creds("mongoDb")?;
    let db = as_str(&c["database"]);
    let uri = if c["configurationType"] == "values" || (as_str(&c["connectionString"]).is_empty() && !as_str(&c["host"]).is_empty()) {
        let enc = |s: &str| s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect::<String>();
        let auth = if as_str(&c["user"]).is_empty() { String::new() } else { format!("{}:{}@", enc(&as_str(&c["user"])), enc(&as_str(&c["password"]))) };
        let port = as_f64(&c["port"]).map(|p| p as u16).unwrap_or(27017);
        let tls = if c["tls"] == true { "?tls=true" } else { "" };
        format!("mongodb://{auth}{}:{port}/{tls}", as_str(&c["host"]))
    } else {
        as_str(&c["connectionString"])
    };
    if db.is_empty() {
        return Err(anyhow!("The MongoDB credential needs a database"));
    }
    let client = mongodb::Client::with_uri_str(&uri).await.map_err(|e| anyhow!("MongoDB connection failed: {e}"))?;
    Ok((client, db))
}

pub async fn run(ctx: &Ctx<'_>) -> Result<Out> {
    let (client, db_name) = connect(ctx).await?;
    let db = client.database(&db_name);
    let resource = ctx.s("resource", 0, "document")?;
    let op = ctx.s("operation", 0, "find")?;
    let coll = db.collection::<Document>(&ctx.s("collection", 0, "")?);
    let mut out = vec![];
    let items = ctx.items().to_vec();

    match (resource.as_str(), op.as_str()) {
        ("document", "find") => {
            for i in 0..items.len() {
                let mut filter = to_doc(&json_param(ctx.val("query", i, json!({}))?, "Query")?, "Query")?;
                object_ids(&mut filter);
                let o = ctx.val("options", i, json!({}))?;
                let mut find = coll.find(filter);
                if let Some(l) = as_f64(&o["limit"]).filter(|l| *l > 0.0) {
                    find = find.limit(l as i64);
                }
                if let Some(s) = as_f64(&o["skip"]).filter(|s| *s > 0.0) {
                    find = find.skip(s as u64);
                }
                if !o["sort"].is_null() && o["sort"] != "" {
                    find = find.sort(to_doc(&json_param(o["sort"].clone(), "Sort")?, "Sort")?);
                }
                if !o["projection"].is_null() && o["projection"] != "" {
                    find = find.projection(to_doc(&json_param(o["projection"].clone(), "Projection")?, "Projection")?);
                }
                let docs: Vec<Document> = find.await?.try_collect().await?;
                out.extend(docs.into_iter().map(|d| Item::paired(to_json(Bson::Document(d)), i)));
            }
        }
        ("document", "aggregate") => {
            for i in 0..items.len() {
                let pipeline = json_param(ctx.val("query", i, json!([]))?, "Pipeline")?;
                let stages: Vec<Document> = pipeline.as_array().ok_or_else(|| anyhow!("The aggregation query must be a JSON array of stages"))?
                    .iter().map(|s| to_doc(s, "Stage")).collect::<Result<_>>()?;
                let docs: Vec<Document> = coll.aggregate(stages).await?.try_collect().await?;
                out.extend(docs.into_iter().map(|d| Item::paired(to_json(Bson::Document(d)), i)));
            }
        }
        ("document", "delete") => {
            for i in 0..items.len() {
                let mut filter = to_doc(&json_param(ctx.val("query", i, json!({}))?, "Query")?, "Query")?;
                object_ids(&mut filter);
                let r = coll.delete_many(filter).await?;
                out.push(Item::paired(json!({"deletedCount": r.deleted_count}), i));
            }
        }
        ("document", "insert") => {
            let mut docs = vec![];
            for i in 0..items.len() {
                let o = ctx.val("options", i, json!({}))?;
                docs.push(item_fields(&items[i].json, &ctx.s("fields", i, "")?, &as_str(&o["dateFields"]), o["useDotNotation"] == true)?);
            }
            if !docs.is_empty() {
                let r = coll.insert_many(docs.clone()).await?;
                for (i, d) in docs.into_iter().enumerate() {
                    let mut v = to_json(Bson::Document(d));
                    if let Some(id) = r.inserted_ids.get(&i) {
                        v["id"] = to_json(id.clone());
                    }
                    out.push(Item::paired(v, i));
                }
            }
        }
        ("document", "update" | "findOneAndUpdate" | "findOneAndReplace") => {
            for i in 0..items.len() {
                let o = ctx.val("options", i, json!({}))?;
                let key = ctx.s("updateKey", i, "_id")?;
                let mut set = item_fields(&items[i].json, &ctx.s("fields", i, "")?, &as_str(&o["dateFields"]), o["useDotNotation"] == true)?;
                let value = items[i].json.get(&key).cloned().ok_or_else(|| anyhow!("Item {i} has no field \"{key}\" to match on"))?;
                let mut filter = doc! { key.as_str(): to_bson(&value)? };
                object_ids(&mut filter);
                set.remove("_id");
                let upsert = ctx.b("upsert", i, false)?;
                match op.as_str() {
                    "update" => {
                        coll.update_one(filter, doc! { "$set": set.clone() }).upsert(upsert).await?;
                    }
                    "findOneAndUpdate" => {
                        coll.find_one_and_update(filter, doc! { "$set": set.clone() }).upsert(upsert).await?;
                    }
                    _ => {
                        coll.find_one_and_replace(filter, set.clone()).upsert(upsert).await?;
                    }
                }
                out.push(Item::paired(to_json(Bson::Document(set)), i));
            }
        }
        ("searchIndexes", _) => {
            let c = ctx.s("collection", 0, "")?;
            for i in 0..items.len() {
                let name = ctx.s("indexNameRequired", i, "")?;
                let definition = || -> Result<Document> { to_doc(&json_param(ctx.val("indexDefinition", i, json!({}))?, "Index definition")?, "Index definition") };
                let v = match op.as_str() {
                    "createSearchIndex" => {
                        let r = db.run_command(doc! { "createSearchIndexes": &c, "indexes": [{ "name": &name, "definition": definition()?, "type": ctx.s("indexType", i, "search")? }] }).await?;
                        to_json(Bson::Document(r))
                    }
                    "dropSearchIndex" => to_json(Bson::Document(db.run_command(doc! { "dropSearchIndex": &c, "name": &name }).await?)),
                    "updateSearchIndex" => to_json(Bson::Document(db.run_command(doc! { "updateSearchIndex": &c, "name": &name, "definition": definition()? }).await?)),
                    "listSearchIndexes" => {
                        let filter = ctx.s("indexName", i, "")?;
                        let stage = if filter.is_empty() { doc! { "$listSearchIndexes": {} } } else { doc! { "$listSearchIndexes": { "name": filter } } };
                        let docs: Vec<Document> = coll.aggregate(vec![stage]).await?.try_collect().await?;
                        out.extend(docs.into_iter().map(|d| Item::paired(to_json(Bson::Document(d)), i)));
                        continue;
                    }
                    o => return Err(anyhow!("MongoDB operation \"{o}\" is not supported")),
                };
                out.push(Item::paired(v, i));
            }
        }
        (r, o) => return Err(anyhow!("MongoDB operation \"{r}:{o}\" is not supported")),
    }
    Ok(vec![out])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bson_to_n8n_json() {
        let id = ObjectId::parse_str("65f000000000000000000001").unwrap();
        let d = doc! { "_id": id, "when": bson::DateTime::from_millis(0), "n": 2_i32, "tags": ["a"] };
        assert_eq!(to_json(Bson::Document(d)), json!({"_id": "65f000000000000000000001", "when": "1970-01-01T00:00:00Z", "n": 2, "tags": ["a"]}));
        let mut f = doc! { "_id": "65f000000000000000000001" };
        object_ids(&mut f);
        assert_eq!(f.get_object_id("_id").unwrap(), id);
    }

    #[test]
    fn picks_fields_and_dates() {
        let d = item_fields(&json!({"a": 1, "b": "x", "at": "2026-01-01T00:00:00Z"}), "a, at", "at", false).unwrap();
        assert_eq!(d.keys().collect::<Vec<_>>(), ["a", "at"]);
        assert!(matches!(d.get("at"), Some(Bson::DateTime(_))));
    }
}
