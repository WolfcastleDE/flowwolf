//! OAuth2 credentials: authorization code flow (with refresh) and client credentials.
//! Any credential type named `oAuth2Api` or `*OAuth2Api` is treated as OAuth2.

use crate::app::App;
use crate::model::*;
use crate::security::random_token;
use anyhow::{Result, anyhow};
use serde_json::{Value, json};

pub fn is_oauth(kind: &str) -> bool {
    kind == "oAuth2Api" || kind.ends_with("OAuth2Api")
}

pub fn redirect_uri(app: &App) -> String {
    format!("{}/rest/oauth2-credential/callback", app.cfg.base_url)
}

/// Stored values merged over the defaults of the credential type (e.g. Google's fixed URLs).
pub fn with_defaults(kind: &str, data: &Value) -> Value {
    let mut d = data.clone();
    if !d.is_object() {
        d = json!({});
    }
    let types = crate::nodes::descriptions()["credentials"].as_array().cloned().unwrap_or_default();
    for t in types.iter().filter(|t| t["name"] == kind || (t["name"] == "oAuth2Api" && is_oauth(kind))) {
        for p in t["properties"].as_array().into_iter().flatten() {
            let n = as_str(&p["name"]);
            if d.get(&n).is_none_or(|v| v.is_null() || (v == "" && p["type"] == "hidden")) {
                d[n.as_str()] = p["default"].clone();
            }
        }
    }
    d
}

fn load(app: &App, id: &str) -> Result<(String, String, Value)> {
    let (name, kind, cipher) = app.db.credential(id)?.ok_or_else(|| anyhow!("Credential not found"))?;
    Ok((name, kind, app.cipher.decrypt(&cipher)?))
}

fn save(app: &App, id: &str, name: &str, kind: &str, data: &Value) -> Result<()> {
    app.db.save_credential(id, name, kind, &app.cipher.encrypt(data)?)
}

/// URL the user opens to grant access. The state ties the callback to the credential.
pub fn auth_url(app: &App, cred_id: &str) -> Result<String> {
    let (_, kind, data) = load(app, cred_id)?;
    let d = with_defaults(&kind, &data);
    let state = random_token();
    // PKCE (RFC 7636, S256): required by X/Twitter, recommended everywhere.
    let verifier = (d["grantType"] == "pkce" || d["pkce"] == true).then(|| format!("{}{}", random_token(), random_token()));
    let mut states = app.oauth_states.lock().unwrap();
    states.retain(|_, (_, at, _)| now_ms() - *at < 600_000);
    states.insert(state.clone(), (cred_id.to_string(), now_ms(), verifier.clone()));
    let mut u = reqwest::Url::parse(&fill_fields(&as_str(&d["authUrl"]), &d)).map_err(|e| anyhow!("Invalid Authorization URL: {e}"))?;
    {
        let mut q = u.query_pairs_mut();
        q.append_pair("client_id", &as_str(&d["clientId"]))
            .append_pair("redirect_uri", &redirect_uri(app))
            .append_pair("response_type", "code")
            .append_pair("state", &state);
        if let Some(v) = &verifier {
            use sha2::Digest;
            let challenge = base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, sha2::Sha256::digest(v.as_bytes()));
            q.append_pair("code_challenge", &challenge).append_pair("code_challenge_method", "S256");
        }
        let scope = as_str(&d["scope"]);
        if !scope.is_empty() {
            q.append_pair("scope", &scope);
        }
        for pair in as_str(&d["authQueryParameters"]).trim_start_matches('?').split('&').filter(|p| !p.is_empty()) {
            let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
            q.append_pair(k, v);
        }
    }
    Ok(u.to_string())
}

/// Replaces "{field}" with the credential field of the same name.
pub fn fill_fields(s: &str, d: &Value) -> String {
    let mut out = s.to_string();
    while let Some(a) = out.find('{') {
        let Some(len) = out[a..].find('}') else { break };
        let v = as_str(&d[&out[a + 1..a + len]]);
        out.replace_range(a..a + len + 1, v.trim_end_matches('/'));
    }
    out
}

async fn token_request(app: &App, d: &Value, params: &[(&str, String)]) -> Result<Value> {
    let mut form: Vec<(String, String)> = params.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    let mut rb = app.http.post(fill_fields(&as_str(&d["accessTokenUrl"]), d)).header("accept", "application/json");
    if d["authentication"] == "body" {
        form.push(("client_id".into(), as_str(&d["clientId"])));
        form.push(("client_secret".into(), as_str(&d["clientSecret"])));
    } else {
        rb = rb.basic_auth(as_str(&d["clientId"]), Some(as_str(&d["clientSecret"])));
    }
    let resp = rb.form(&form).send().await.map_err(|e| anyhow!("Token request failed: {e}"))?;
    let status = resp.status();
    let text = resp.text().await?;
    let mut v: Value = serde_json::from_str(&text).unwrap_or_else(|_| {
        // Some providers answer form encoded.
        Value::Object(text.split('&').filter_map(|p| p.split_once('=')).map(|(k, v)| (k.to_string(), json!(v))).collect())
    });
    if !status.is_success() || v.get("access_token").is_none() {
        return Err(anyhow!("Token request failed ({}): {}", status.as_u16(), text.chars().take(300).collect::<String>()));
    }
    if let Some(exp) = as_f64(&v["expires_in"]) {
        v["expires_at"] = json!(now_ms() + (exp * 1000.0) as i64);
    }
    Ok(v)
}

/// Handles the provider redirect and stores the tokens.
pub async fn callback(app: &App, code: &str, state: &str) -> Result<()> {
    let (id, at, verifier) = app.oauth_states.lock().unwrap().remove(state).ok_or_else(|| anyhow!("Unknown or expired OAuth state, please try connecting again"))?;
    if now_ms() - at > 600_000 {
        return Err(anyhow!("The authorization took too long, please try again"));
    }
    let (name, kind, mut data) = load(app, &id)?;
    let d = with_defaults(&kind, &data);
    let mut params = vec![("grant_type", "authorization_code".to_string()), ("code", code.into()), ("redirect_uri", redirect_uri(app))];
    if let Some(v) = verifier {
        params.push(("code_verifier", v));
    }
    let tok = token_request(app, &d, &params).await?;
    data["oauthTokenData"] = tok;
    save(app, &id, &name, &kind, &data)
}

/// Returns a valid access token, refreshing or fetching it when needed.
pub async fn access_token(app: &App, cred_id: &str) -> Result<String> {
    let (name, kind, mut data) = load(app, cred_id)?;
    let d = with_defaults(&kind, &data);
    let t = &data["oauthTokenData"];
    let fresh = |t: &Value| t["access_token"].is_string() && as_f64(&t["expires_at"]).is_none_or(|e| e as i64 > now_ms() + 60_000);
    if fresh(t) {
        return Ok(as_str(&t["access_token"]));
    }
    let tok = if d["grantType"] == "clientCredentials" {
        let mut p = vec![("grant_type", "client_credentials".to_string())];
        if !as_str(&d["scope"]).is_empty() {
            p.push(("scope", as_str(&d["scope"])));
        }
        token_request(app, &d, &p).await?
    } else {
        let refresh = as_str(&t["refresh_token"]);
        if t.is_null() {
            return Err(anyhow!("Credential \"{name}\" is not connected yet. Open it and click 'Connect account'"));
        }
        if refresh.is_empty() {
            return Err(anyhow!("The access token of \"{name}\" expired and no refresh token is available. Reconnect the credential"));
        }
        let mut n = token_request(app, &d, &[("grant_type", "refresh_token".into()), ("refresh_token", refresh.clone())]).await?;
        if n.get("refresh_token").is_none() {
            n["refresh_token"] = json!(refresh);
        }
        n
    };
    let access = as_str(&tok["access_token"]);
    data["oauthTokenData"] = tok;
    save(app, cred_id, &name, &kind, &data)?;
    Ok(access)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_oauth_types() {
        assert!(is_oauth("oAuth2Api") && is_oauth("googleSheetsOAuth2Api") && !is_oauth("httpBasicAuth"));
        let d = with_defaults("googleSheetsOAuth2Api", &json!({"clientId": "x"}));
        assert_eq!(d["authUrl"], json!("https://accounts.google.com/o/oauth2/v2/auth"));
        assert_eq!(d["clientId"], json!("x"));
    }

    #[test]
    fn fills_credential_fields() {
        let d = json!({"url": "https://shop.example/", "email": "a@b.c"});
        assert_eq!(fill_fields("{url}/api/oauth/token", &d), "https://shop.example/api/oauth/token");
        assert_eq!(fill_fields("{email}/token", &d), "a@b.c/token");
    }
}
