//! AWS S3 (and S3-compatible storage such as MinIO, R2 or Wasabi via a custom endpoint):
//! buckets, files and folders, signed with AWS Signature V4.

use super::data::{binary_bytes, make_binary, mime_for};
use super::declarative::{aws_encode, sign_v4};
use super::extra::xml_to_json;
use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use serde_json::{Value, json};
use std::time::Duration;

struct S3<'a> {
    ctx: &'a Ctx<'a>,
    creds: Value,
    /// Custom endpoint (path-style URLs) or None for AWS (virtual-hosted URLs).
    endpoint: Option<String>,
    region: String,
}

fn list_of(v: &Value) -> Vec<Value> {
    match v {
        Value::Array(a) => a.clone(),
        Value::Null => vec![],
        v => vec![v.clone()],
    }
}

/// Canned ACL names as n8n stores them (camelCase) to the S3 header values.
fn acl(v: &str) -> String {
    match v {
        "authenticatedRead" => "authenticated-read",
        "awsExecRead" => "aws-exec-read",
        "bucketOwnerFullControl" => "bucket-owner-full-control",
        "bucketOwnerRead" => "bucket-owner-read",
        "private" => "private",
        "publicRead" => "public-read",
        "publicReadWrite" => "public-read-write",
        other => other,
    }
    .to_string()
}

/// "glacierDeepArchive" -> "GLACIER_DEEP_ARCHIVE" (n8n option values to S3 enum values).
fn upper_snake(v: &str) -> String {
    let mut out = String::new();
    for c in v.chars() {
        if c.is_uppercase() && !out.is_empty() {
            out.push('_');
        }
        out.push(c.to_ascii_uppercase());
    }
    out
}

impl<'a> S3<'a> {
    fn new(ctx: &'a Ctx<'a>) -> Result<Self> {
        let creds = ctx.creds("aws")?;
        let region = Some(as_str(&creds["region"])).filter(|r| !r.is_empty()).unwrap_or_else(|| "us-east-1".into());
        let endpoint = Some(as_str(&creds["s3Endpoint"]).trim_end_matches('/').to_string())
            .filter(|e| !e.is_empty() && creds["customEndpoints"] != false);
        Ok(S3 { ctx, creds, endpoint, region })
    }

    fn url(&self, bucket: &str, key: &str, query: &[(&str, String)]) -> String {
        let key: String = key.split('/').map(aws_encode).collect::<Vec<_>>().join("/");
        let base = match (&self.endpoint, bucket.is_empty()) {
            (Some(e), true) => format!("{e}/"),
            (Some(e), false) => format!("{e}/{bucket}/{key}"),
            (None, true) => format!("https://s3.{}.amazonaws.com/", self.region),
            (None, false) => format!("https://{bucket}.s3.{}.amazonaws.com/{key}", self.region),
        };
        // Pre-encoded the AWS way (%20, not +) so the signature matches what S3 computes.
        let q: Vec<String> = query.iter().map(|(k, v)| if v.is_empty() && *k == "delete" { k.to_string() } else { format!("{}={}", aws_encode(k), aws_encode(v)) }).collect();
        if q.is_empty() { base } else { format!("{base}?{}", q.join("&")) }
    }

    async fn send(&self, method: reqwest::Method, bucket: &str, key: &str, query: &[(&str, String)], headers: &[(String, String)], body: Vec<u8>) -> Result<(reqwest::header::HeaderMap, Vec<u8>)> {
        let mut rb = self.ctx.eng.app.http.request(method, self.url(bucket, key, query)).timeout(Duration::from_secs(600));
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let mut req = rb.body(body).build()?;
        let mut creds = self.creds.clone();
        creds["region"] = json!(self.region);
        sign_v4(&mut req, &creds, "s3", chrono::Utc::now())?;
        let resp = self.ctx.eng.app.http.execute(req).await.map_err(|e| anyhow!("S3 not reachable: {e}"))?;
        let status = resp.status();
        let h = resp.headers().clone();
        let bytes = resp.bytes().await?.to_vec();
        if !status.is_success() {
            let err = xml_to_json(&String::from_utf8_lossy(&bytes)).unwrap_or_default();
            let e = &err["Error"];
            return Err(anyhow!("S3 returned {}: {} {}", status.as_u16(), as_str(&e["Code"]), as_str(&e["Message"])));
        }
        Ok((h, bytes))
    }

    async fn xml(&self, method: reqwest::Method, bucket: &str, key: &str, query: &[(&str, String)], headers: &[(String, String)], body: Vec<u8>) -> Result<Value> {
        let (_, bytes) = self.send(method, bucket, key, query, headers, body).await?;
        if bytes.is_empty() {
            return Ok(json!({}));
        }
        xml_to_json(&String::from_utf8_lossy(&bytes))
    }

    /// ListObjectsV2 with paging; `limit` None lists everything.
    async fn list(&self, bucket: &str, mut query: Vec<(&'static str, String)>, limit: Option<usize>) -> Result<Vec<Value>> {
        query.push(("list-type", "2".into()));
        let mut out = vec![];
        let mut token: Option<String> = None;
        loop {
            let mut q = query.clone();
            if let Some(l) = limit {
                q.push(("max-keys", (l - out.len()).min(1000).to_string()));
            }
            if let Some(t) = &token {
                q.push(("continuation-token", t.clone()));
            }
            let v = self.xml(reqwest::Method::GET, bucket, "", &q, &[], vec![]).await?;
            let r = &v["ListBucketResult"];
            out.extend(list_of(&r["Contents"]));
            token = Some(as_str(&r["NextContinuationToken"])).filter(|t| !t.is_empty() && r["IsTruncated"] == "true");
            if token.is_none() || limit.is_some_and(|l| out.len() >= l) {
                break;
            }
        }
        if let Some(l) = limit {
            out.truncate(l);
        }
        Ok(out)
    }

    /// Optional request headers shared by upload, copy and folder creation.
    fn object_headers(&self, f: &Value) -> Vec<(String, String)> {
        let mut h = vec![];
        let mut set = |k: &str, v: String| {
            if !v.is_empty() {
                h.push((k.to_string(), v));
            }
        };
        set("x-amz-acl", f["acl"].as_str().map(acl).unwrap_or_default());
        set("x-amz-storage-class", f["storageClass"].as_str().map(upper_snake).unwrap_or_default());
        set("x-amz-server-side-encryption", as_str(&f["serverSideEncryption"]));
        set("x-amz-server-side-encryption-aws-kms-key-id", as_str(&f["encryptionAwsKmsKeyId"]));
        set("x-amz-server-side-encryption-context", as_str(&f["serverSideEncryptionContext"]));
        set("x-amz-server-side-encryption-customer-algorithm", as_str(&f["serversideEncryptionCustomerAlgorithm"]));
        set("x-amz-server-side-encryption-customer-key", as_str(&f["serversideEncryptionCustomerKey"]));
        set("x-amz-object-lock-legal-hold", f["lockLegalHold"].as_bool().map(|b| if b { "ON" } else { "OFF" }.to_string()).unwrap_or_default());
        set("x-amz-object-lock-mode", f["lockMode"].as_str().map(|m| m.to_uppercase()).unwrap_or_default());
        set("x-amz-object-lock-retain-until-date", as_str(&f["lockRetainUntilDate"]));
        set("x-amz-metadata-directive", f["metadataDirective"].as_str().map(|m| m.to_uppercase()).unwrap_or_default());
        set("x-amz-tagging-directive", f["taggingDirective"].as_str().map(|m| m.to_uppercase()).unwrap_or_default());
        set("x-amz-request-payer", if f["requesterPays"] == true { "requester".into() } else { String::new() });
        for (k, hdr) in [("grantFullControl", "x-amz-grant-full-control"), ("grantRead", "x-amz-grant-read"), ("grantReadAcp", "x-amz-grant-read-acp"),
                         ("grantWrite", "x-amz-grant-write"), ("grantWriteAcp", "x-amz-grant-write-acp")] {
            if f[k] == true {
                set(hdr, format!("id={}", as_str(&self.creds["accessKeyId"])));
            }
        }
        h
    }
}

fn split_path(p: &str) -> (String, String) {
    let p = p.trim_start_matches('/');
    let (b, k) = p.split_once('/').unwrap_or((p, ""));
    (b.to_string(), k.to_string())
}

fn limit_of(ctx: &Ctx, i: usize) -> Result<Option<usize>> {
    Ok(if ctx.b("returnAll", i, false)? { None } else { Some(ctx.n("limit", i, 100.0)? as usize) })
}

pub async fn run(ctx: &Ctx<'_>) -> Result<Out> {
    let s3 = S3::new(ctx)?;
    let (resource, op) = (ctx.s("resource", 0, "file")?, ctx.s("operation", 0, "download")?);
    let ok = || json!({"success": true});
    let mut out = vec![];
    use reqwest::Method as M;
    for i in 0..ctx.items().len() {
        let af = ctx.val("additionalFields", i, json!({}))?;
        let opts = ctx.val("options", i, json!({}))?;
        let bucket = ctx.s("bucketName", i, "")?;
        match (resource.as_str(), op.as_str()) {
            ("bucket", "create") => {
                let name = ctx.s("name", i, "")?;
                let region = Some(as_str(&af["region"])).filter(|r| !r.is_empty()).unwrap_or(s3.region.clone());
                let body = if region == "us-east-1" { vec![] } else {
                    format!("<CreateBucketConfiguration xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><LocationConstraint>{region}</LocationConstraint></CreateBucketConfiguration>").into_bytes()
                };
                let mut h = s3.object_headers(&af);
                if af["bucketObjectLockEnabled"] == true {
                    h.push(("x-amz-bucket-object-lock-enabled".into(), "true".into()));
                }
                s3.send(M::PUT, &name, "", &[], &h, body).await?;
                out.push(Item::paired(ok(), i));
            }
            ("bucket", "delete") => {
                s3.send(M::DELETE, &ctx.s("name", i, "")?, "", &[], &[], vec![]).await?;
                out.push(Item::paired(ok(), i));
            }
            ("bucket", "getAll") => {
                let v = s3.xml(M::GET, "", "", &[], &[], vec![]).await?;
                let mut buckets = list_of(&v["ListAllMyBucketsResult"]["Buckets"]["Bucket"]);
                if let Some(l) = limit_of(ctx, i)? {
                    buckets.truncate(l);
                }
                out.extend(buckets.into_iter().map(|b| Item::paired(b, i)));
            }
            ("bucket", "search") => {
                let mut q = vec![];
                for (k, api) in [("prefix", "prefix"), ("delimiter", "delimiter"), ("encodingType", "encoding-type"), ("startAfter", "start-after")] {
                    if !as_str(&af[k]).is_empty() {
                        q.push((api, as_str(&af[k])));
                    }
                }
                if af["fetchOwner"] == true {
                    q.push(("fetch-owner", "true".into()));
                }
                out.extend(s3.list(&bucket, q, limit_of(ctx, i)?).await?.into_iter().map(|o| Item::paired(o, i)));
            }
            ("file", "upload") => {
                let parent = as_str(&af["parentFolderKey"]).trim_matches('/').to_string();
                let name = ctx.s("fileName", i, "")?;
                let (data, mime, name) = if ctx.b("binaryData", i, true)? {
                    let field = ctx.s("binaryPropertyName", i, "data")?;
                    let bin = ctx.items()[i].binary.as_ref().and_then(|b| b.get(&field)).ok_or_else(|| anyhow!("No binary property \"{field}\""))?;
                    let name = if name.is_empty() { as_str(&bin["fileName"]) } else { name };
                    (binary_bytes(bin)?, bin["mimeType"].as_str().filter(|m| !m.is_empty()).map(String::from).unwrap_or_else(|| mime_for(&name).to_string()), name)
                } else {
                    (ctx.s("fileContent", i, "")?.into_bytes(), mime_for(&name).to_string(), name)
                };
                if name.is_empty() {
                    return Err(anyhow!("File Name is required"));
                }
                let key = if parent.is_empty() { name } else { format!("{parent}/{name}") };
                let mut h = s3.object_headers(&af);
                h.push(("content-type".into(), mime));
                let tags: Vec<String> = ctx.val("tagsUi", i, json!({}))?["tagsValues"].as_array().into_iter().flatten()
                    .map(|t| format!("{}={}", aws_encode(&as_str(&t["key"])), aws_encode(&as_str(&t["value"])))).collect();
                if !tags.is_empty() {
                    h.push(("x-amz-tagging".into(), tags.join("&")));
                }
                s3.send(M::PUT, &bucket, &key, &[], &h, data).await?;
                out.push(Item::paired(ok(), i));
            }
            ("file", "download") => {
                let key = ctx.s("fileKey", i, "")?;
                let (h, bytes) = s3.send(M::GET, &bucket, &key, &[], &[], vec![]).await?;
                let name = key.rsplit('/').next().unwrap_or("file").to_string();
                let mime = h.get("content-type").and_then(|v| v.to_str().ok()).filter(|m| !m.is_empty() && *m != "binary/octet-stream").map(String::from)
                    .unwrap_or_else(|| mime_for(&name).to_string());
                let mut item = ctx.items()[i].clone();
                item.paired_item = Some(json!({"item": i}));
                let mut bin = item.binary.take().unwrap_or_default();
                bin.insert(ctx.s("binaryPropertyName", i, "data")?, make_binary(&bytes, &mime, &name));
                item.binary = Some(bin);
                out.push(item);
            }
            ("file", "copy") => {
                let (src_bucket, src_key) = split_path(&ctx.s("sourcePath", i, "")?);
                let (dst_bucket, dst_key) = split_path(&ctx.s("destinationPath", i, "")?);
                let mut h = s3.object_headers(&af);
                let source = format!("/{src_bucket}/{}", src_key.split('/').map(aws_encode).collect::<Vec<_>>().join("/"));
                h.push(("x-amz-copy-source".into(), source));
                let v = s3.xml(M::PUT, &dst_bucket, &dst_key, &[], &h, vec![]).await?;
                out.push(Item::paired(v.get("CopyObjectResult").cloned().unwrap_or(v), i));
            }
            ("file", "delete") => {
                let q: Vec<(&str, String)> = if as_str(&opts["versionId"]).is_empty() { vec![] } else { vec![("versionId", as_str(&opts["versionId"]))] };
                s3.send(M::DELETE, &bucket, &ctx.s("fileKey", i, "")?, &q, &[], vec![]).await?;
                out.push(Item::paired(ok(), i));
            }
            ("file", "getAll") | ("folder", "getAll") => {
                let mut q = vec![];
                let prefix = as_str(&opts["folderKey"]);
                if !prefix.is_empty() {
                    q.push(("prefix", format!("{}/", prefix.trim_end_matches('/'))));
                }
                if opts["fetchOwner"] == true {
                    q.push(("fetch-owner", "true".into()));
                }
                let folders = resource == "folder";
                // Folders are the zero-byte keys ending in "/"; files are everything else.
                let all = s3.list(&bucket, q, None).await?;
                let mut picked: Vec<Value> = all.into_iter().filter(|o| as_str(&o["Key"]).ends_with('/') == folders && as_str(&o["Key"]) != format!("{}/", prefix.trim_end_matches('/'))).collect();
                if let Some(l) = limit_of(ctx, i)? {
                    picked.truncate(l);
                }
                out.extend(picked.into_iter().map(|o| Item::paired(o, i)));
            }
            ("folder", "create") => {
                let parent = as_str(&af["parentFolderKey"]).trim_matches('/').to_string();
                let name = ctx.s("folderName", i, "")?.trim_matches('/').to_string();
                let key = if parent.is_empty() { format!("{name}/") } else { format!("{parent}/{name}/") };
                s3.send(M::PUT, &bucket, &key, &[], &s3.object_headers(&af), vec![]).await?;
                out.push(Item::paired(ok(), i));
            }
            ("folder", "delete") => {
                let prefix = format!("{}/", ctx.s("folderKey", i, "")?.trim_matches('/'));
                let keys: Vec<String> = s3.list(&bucket, vec![("prefix", prefix.clone())], None).await?.iter().map(|o| as_str(&o["Key"])).collect();
                // DeleteObjects takes up to 1000 keys per request and needs a Content-MD5.
                for chunk in keys.chunks(1000) {
                    let body = format!("<Delete><Quiet>true</Quiet>{}</Delete>", chunk.iter().map(|k| format!("<Object><Key>{}</Key></Object>",
                        k.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;"))).collect::<String>());
                    use md5::Digest;
                    let md5 = base64::engine::general_purpose::STANDARD.encode(md5::Md5::digest(body.as_bytes()));
                    s3.send(M::POST, &bucket, "", &[("delete", String::new())], &[("content-md5".into(), md5), ("content-type".into(), "application/xml".into())], body.into_bytes()).await?;
                }
                out.push(Item::paired(ok(), i));
            }
            (r, o) => return Err(anyhow!("AWS S3 operation \"{r}:{o}\" is not supported")),
        }
    }
    Ok(vec![out])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn option_values_to_s3() {
        assert_eq!(acl("publicRead"), "public-read");
        assert_eq!(upper_snake("glacierDeepArchive"), "GLACIER_DEEP_ARCHIVE");
        assert_eq!(upper_snake("standard"), "STANDARD");
        assert_eq!(split_path("/bucket/a/b.txt"), ("bucket".into(), "a/b.txt".into()));
    }
}
