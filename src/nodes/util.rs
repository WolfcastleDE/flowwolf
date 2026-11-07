//! Small utility nodes: Compare Datasets, JWT and TOTP.

use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64URL;
use serde_json::{Map, Value, json};

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    Some(match kind {
        "n8n-nodes-base.compareDatasets" => compare(ctx),
        "n8n-nodes-base.jwt" => jwt(ctx),
        "n8n-nodes-base.totp" => totp(ctx),
        _ => return None,
    })
}

// ---------------------------------------------------------------------------
// Compare Datasets: outputs "In A only", "Same", "Different", "In B only"
// ---------------------------------------------------------------------------

fn compare(ctx: &Ctx) -> Result<Out> {
    let pairs: Vec<(String, String)> = ctx.list("mergeByFields.values", 0)?.iter().map(|p| (as_str(&p["field1"]), as_str(&p["field2"]))).collect();
    if pairs.is_empty() {
        return Err(anyhow!("Define at least one pair of fields to match"));
    }
    let skip: Vec<String> = ctx.s("options.skipFields", 0, "")?.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    let resolve = ctx.s("resolve", 0, "includeBoth")?;
    let (a, b) = (ctx.input(0), ctx.input(1));
    let key = |j: &Value, side: usize| -> String {
        serde_json::to_string(&pairs.iter().map(|p| get_path(j, if side == 0 { &p.0 } else { &p.1 }).cloned().unwrap_or(Value::Null)).collect::<Vec<_>>()).unwrap()
    };
    let strip = |j: &Value| -> Value {
        let mut j = j.clone();
        skip.iter().for_each(|f| remove_path(&mut j, f));
        j
    };
    let mut used = vec![false; b.len()];
    let (mut only_a, mut same, mut diff) = (vec![], vec![], vec![]);
    for x in &a {
        let k = key(&x.json, 0);
        let Some(j) = (0..b.len()).find(|j| !used[*j] && key(&b[*j].json, 1) == k) else {
            only_a.push(x.clone());
            continue;
        };
        used[j] = true;
        let (ja, jb) = (strip(&x.json), strip(&b[j].json));
        if ja == jb {
            same.push(x.clone());
            continue;
        }
        diff.push(Item::new(match resolve.as_str() {
            "preferInput1" => x.json.clone(),
            "preferInput2" => b[j].json.clone(),
            _ => {
                let (oa, ob) = (ja.as_object().cloned().unwrap_or_default(), jb.as_object().cloned().unwrap_or_default());
                let (mut s, mut d) = (Map::new(), Map::new());
                for k in oa.keys().chain(ob.keys().filter(|k| !oa.contains_key(*k))) {
                    let (va, vb) = (oa.get(k).cloned().unwrap_or(Value::Null), ob.get(k).cloned().unwrap_or(Value::Null));
                    if va == vb { s.insert(k.clone(), va); } else { d.insert(k.clone(), json!({"inputA": va, "inputB": vb})); }
                }
                let keys_obj: Map<String, Value> = pairs.iter().map(|p| (p.0.clone(), get_path(&x.json, &p.0).cloned().unwrap_or(Value::Null))).collect();
                json!({"keys": keys_obj, "same": s, "different": d})
            }
        }));
    }
    let only_b = b.iter().enumerate().filter(|(j, _)| !used[*j]).map(|(_, y)| y.clone()).collect();
    Ok(vec![only_a, same, diff, only_b])
}

// ---------------------------------------------------------------------------
// JWT (HMAC algorithms)
// ---------------------------------------------------------------------------

fn alg_hash(alg: &str) -> Result<&'static str> {
    Ok(match alg {
        "HS256" => "SHA256",
        "HS384" => "SHA384",
        "HS512" => "SHA512",
        a => return Err(anyhow!("Algorithm {a} is not supported (use HS256, HS384 or HS512)")),
    })
}

pub fn jwt_sign(payload: &Value, secret: &str, alg: &str) -> Result<String> {
    let head = B64URL.encode(json!({"alg": alg, "typ": "JWT"}).to_string());
    let body = B64URL.encode(payload.to_string());
    let sig = super::data::hmac(alg_hash(alg)?, secret.as_bytes(), format!("{head}.{body}").as_bytes())?;
    Ok(format!("{head}.{body}.{}", B64URL.encode(sig)))
}

/// Returns (header, payload); verifies signature and expiry when `secret` is given.
pub fn jwt_decode(token: &str, verify: Option<(&str, &str)>) -> Result<(Value, Value)> {
    let parts: Vec<&str> = token.trim().split('.').collect();
    if parts.len() != 3 {
        return Err(anyhow!("Invalid token"));
    }
    let header: Value = serde_json::from_slice(&B64URL.decode(parts[0])?)?;
    let payload: Value = serde_json::from_slice(&B64URL.decode(parts[1])?)?;
    if let Some((secret, alg)) = verify {
        if header["alg"] != alg {
            return Err(anyhow!("Token algorithm {} does not match {alg}", as_str(&header["alg"])));
        }
        let expected = super::data::hmac(alg_hash(alg)?, secret.as_bytes(), format!("{}.{}", parts[0], parts[1]).as_bytes())?;
        let given = B64URL.decode(parts[2])?;
        if expected.len() != given.len() || expected.iter().zip(&given).fold(0u8, |acc, (x, y)| acc | (x ^ y)) != 0 {
            return Err(anyhow!("invalid signature"));
        }
        if as_f64(&payload["exp"]).is_some_and(|e| (e as i64) < now_ms() / 1000) {
            return Err(anyhow!("jwt expired"));
        }
    }
    Ok((header, payload))
}

fn jwt(ctx: &Ctx) -> Result<Out> {
    let c = ctx.creds("jwtAuth")?;
    let (secret, alg) = (as_str(&c["secret"]), c["algorithm"].as_str().unwrap_or("HS256").to_string());
    let op = ctx.s("operation", 0, "sign")?;
    let mut out = vec![];
    for i in 0..ctx.items().len() {
        let j = match op.as_str() {
            "sign" => {
                let mut claims = match ctx.val("claimsJson", i, json!({}))? {
                    Value::String(s) if s.trim().is_empty() => json!({}),
                    Value::String(s) => serde_json::from_str(&s).map_err(|e| anyhow!("Claims must be valid JSON: {e}"))?,
                    v => v,
                };
                let now = now_ms() / 1000;
                if claims.get("iat").is_none() {
                    claims["iat"] = json!(now);
                }
                if let Some(exp) = ctx.get("options.expiresIn", i)?.and_then(|v| as_f64(&v)).filter(|e| *e > 0.0) {
                    claims["exp"] = json!(now + exp as i64);
                }
                json!({"token": jwt_sign(&claims, &secret, &alg)?})
            }
            "decode" => {
                let (h, p) = jwt_decode(&ctx.s("token", i, "")?, None)?;
                if ctx.b("options.complete", i, false)? { json!({"header": h, "payload": p}) } else { p }
            }
            "verify" => {
                let (h, p) = jwt_decode(&ctx.s("token", i, "")?, Some((&secret, &alg)))?;
                if ctx.b("options.complete", i, false)? { json!({"header": h, "payload": p}) } else { p }
            }
            o => return Err(anyhow!("Operation {o} is not supported")),
        };
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// TOTP (RFC 6238)
// ---------------------------------------------------------------------------

pub fn base32_decode(s: &str) -> Result<Vec<u8>> {
    const ALPHA: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    let (mut bits, mut acc, mut out) = (0u32, 0u32, vec![]);
    for ch in s.chars().filter(|c| !c.is_whitespace() && *c != '=' && *c != '-') {
        let v = ALPHA.iter().position(|a| *a as char == ch.to_ascii_uppercase()).ok_or_else(|| anyhow!("Invalid base32 secret"))? as u32;
        acc = (acc << 5) | v;
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Ok(out)
}

pub fn totp_at(secret: &[u8], unix: i64, period: i64, digits: u32, alg: &str) -> Result<String> {
    let counter = (unix / period) as u64;
    let mac = super::data::hmac(alg, secret, &counter.to_be_bytes())?;
    let off = (mac[mac.len() - 1] & 0x0f) as usize;
    let bin = u32::from_be_bytes([mac[off] & 0x7f, mac[off + 1], mac[off + 2], mac[off + 3]]);
    Ok(format!("{:0width$}", bin % 10u32.pow(digits), width = digits as usize))
}

fn totp(ctx: &Ctx) -> Result<Out> {
    let c = ctx.creds("totpApi")?;
    let secret = base32_decode(&as_str(&c["secret"]))?;
    let period = ctx.n("options.period", 0, 30.0)?.max(1.0) as i64;
    let digits = ctx.n("options.digits", 0, 6.0)?.clamp(6.0, 8.0) as u32;
    let alg = ctx.s("options.algorithm", 0, "SHA1")?;
    let now = now_ms() / 1000;
    let token = totp_at(&secret, now, period, digits, &alg)?;
    Ok(vec![ctx.items().iter().enumerate().map(|(i, _)| Item::paired(json!({"token": token, "secondsRemaining": period - now % period}), i)).collect()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jwt_and_totp() {
        let t = jwt_sign(&json!({"sub": "42"}), "secret", "HS256").unwrap();
        assert_eq!(jwt_decode(&t, Some(("secret", "HS256"))).unwrap().1["sub"], json!("42"));
        assert!(jwt_decode(&t, Some(("wrong", "HS256"))).is_err());
        let expired = jwt_sign(&json!({"exp": 1}), "secret", "HS256").unwrap();
        assert!(jwt_decode(&expired, Some(("secret", "HS256"))).unwrap_err().to_string().contains("expired"));
        // RFC 6238 test vector (SHA1, 8 digits, T = 59).
        let key = b"12345678901234567890";
        assert_eq!(totp_at(key, 59, 30, 8, "SHA1").unwrap(), "94287082");
        assert_eq!(base32_decode("GEZDGNBV").unwrap(), b"12345");
    }
}
