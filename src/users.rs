//! User management beyond login: invitations, roles, password reset, deleting users with a data
//! transfer and two-factor authentication (TOTP with recovery codes). Endpoints follow n8n's REST API.

use crate::api::{ApiErr, R, User, USER_COLS, check_password, err, ok, require_owner, session_response, user_row};
use crate::app::AppState;
use crate::model::*;
use crate::p;
use crate::security::{hash_password, random_token, sha256_hex};
use axum::Extension;
use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::time::Duration;

const INVITE_DAYS: i64 = 7;
const RESET_MINUTES: i64 = 20;

/// Columns added for user management (see `Db::migrate`).
pub const USER_MIGRATIONS: &[(&str, &str)] = &[
    ("mfa_secret", "TEXT"),
    ("mfa_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("mfa_recovery", "TEXT"),
    ("invite_token", "TEXT"),
    ("invite_expires", "INTEGER"),
    ("invited_by", "TEXT"),
    ("reset_token", "TEXT"),
    ("reset_expires", "INTEGER"),
];

fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

/// "global:admin" (n8n) or "admin" -> "admin"; only admin and member can be given.
fn role_name(v: &Value) -> Result<&'static str, ApiErr> {
    match as_str(v).trim_start_matches("global:") {
        "admin" => Ok("admin"),
        "member" | "" => Ok("member"),
        r => Err(err(StatusCode::BAD_REQUEST, format!("Role \"{r}\" cannot be assigned"))),
    }
}

/// Users as the editor lists them, with pending invitations and 2FA state.
pub async fn list_users(State(app): State<AppState>) -> R {
    let rows = app.db.rows(&format!("SELECT {USER_COLS}, u.password, u.mfa_enabled, u.created_at FROM users u ORDER BY u.created_at"), &[])?;
    ok(rows.iter().map(|r| {
        let u = user_row(r);
        json!({"id": u.id, "email": u.email, "firstName": u.first_name, "lastName": u.last_name, "role": u.role,
               "isPending": r[5].s().is_empty(), "mfaEnabled": r[6].i() == 1, "createdAt": r[7].s()})
    }).collect::<Vec<_>>())
}

// ---------------------------------------------------------------------------
// mail
// ---------------------------------------------------------------------------

/// Sends a user-management mail when SMTP is configured (n8n's N8N_SMTP_* variables).
/// Returns false without SMTP, so callers hand out the link instead.
async fn send_mail(to: &str, subject: &str, html: String) -> bool {
    use lettre::message::header::ContentType;
    use lettre::transport::smtp::authentication::Credentials;
    use lettre::transport::smtp::client::{Tls, TlsParameters};
    use lettre::{AsyncSmtpTransport, AsyncTransport, Message, Tokio1Executor};
    let env = |k: &str| std::env::var(k).unwrap_or_default();
    let host = env("N8N_SMTP_HOST");
    if host.is_empty() {
        return false;
    }
    let port: u16 = env("N8N_SMTP_PORT").parse().unwrap_or(465);
    let Ok(tls) = TlsParameters::new(host.clone()) else { return false };
    let mut b = AsyncSmtpTransport::<Tokio1Executor>::builder_dangerous(&host).port(port)
        .tls(match env("N8N_SMTP_SSL").as_str() { "false" => if env("N8N_SMTP_STARTTLS") == "false" { Tls::None } else { Tls::Opportunistic(tls) }, _ => Tls::Wrapper(tls) });
    if !env("N8N_SMTP_USER").is_empty() {
        b = b.credentials(Credentials::new(env("N8N_SMTP_USER"), env("N8N_SMTP_PASS")));
    }
    let sender = Some(env("N8N_SMTP_SENDER")).filter(|s| !s.is_empty()).unwrap_or_else(|| format!("Flowwolf <noreply@{host}>"));
    let Ok(from) = sender.parse() else { return false };
    let Ok(to) = to.parse() else { return false };
    let Ok(msg) = Message::builder().from(from).to(to).subject(subject).header(ContentType::TEXT_HTML).body(html) else { return false };
    match b.timeout(Some(Duration::from_secs(20))).build().send(msg).await {
        Ok(_) => true,
        Err(e) => {
            tracing::warn!("user management mail to {} failed: {e}", subject);
            false
        }
    }
}

fn mail_html(title: &str, text: &str, link: &str, button: &str) -> String {
    let esc = |s: &str| s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    format!("<div style=\"font-family:system-ui,sans-serif;max-width:480px;margin:auto;padding:24px;color:#16181d\"><h2 style=\"margin:0 0 12px\">{}</h2>\
             <p style=\"line-height:1.5\">{}</p><p><a href=\"{}\" style=\"display:inline-block;background:#2f5bea;color:#fff;padding:10px 16px;border-radius:8px;\
             text-decoration:none\">{}</a></p><p style=\"color:#667;font-size:13px\">{}</p></div>", esc(title), esc(text), esc(link), esc(button), esc(link))
}

// ---------------------------------------------------------------------------
// invitations
// ---------------------------------------------------------------------------

async fn invite_one(app: &AppState, by: &User, email: &str, role: &str) -> Result<Value, ApiErr> {
    let email = email.trim().to_lowercase();
    if !email.contains('@') {
        return Err(err(StatusCode::BAD_REQUEST, format!("Invalid email \"{email}\"")));
    }
    let token = random_token();
    let id = short_id();
    app.db.exec("INSERT INTO users(id, email, first_name, last_name, password, role, created_at, invite_token, invite_expires, invited_by) \
                 VALUES(?1, ?2, '', '', '', ?3, ?4, ?5, ?6, ?7)",
                p![&id, &email, role, now_iso(), sha256_hex(&token), now_ms() + INVITE_DAYS * 86_400_000, &by.id])
        .map_err(|_| err(StatusCode::BAD_REQUEST, format!("A user with the email {email} already exists")))?;
    let url = format!("{}/app/#/signup?token={token}", app.cfg.base_url);
    let who = [by.first_name.as_str(), by.last_name.as_str()].join(" ").trim().to_string();
    let who = if who.is_empty() { by.email.clone() } else { who };
    let sent = send_mail(&email, "You have been invited to Flowwolf",
                         mail_html("Join Flowwolf", &format!("{who} invited you to their Flowwolf instance."), &url, "Accept invitation")).await;
    Ok(json!({"user": {"id": id, "email": email, "role": role, "inviteAcceptUrl": url, "emailSent": sent}}))
}

/// POST /invitations — [{email, role}] like n8n (a single object works too).
pub async fn invite(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    require_owner(&u)?;
    let list = if b.is_array() { b.as_array().cloned().unwrap_or_default() } else { vec![b] };
    let mut out = vec![];
    for x in list {
        let role = role_name(&x["role"])?;
        match invite_one(&app, &u, &as_str(&x["email"]), role).await {
            Ok(v) => out.push(v),
            Err(e) => out.push(json!({"user": {"email": as_str(&x["email"])}, "error": e.1})),
        }
    }
    ok(out)
}

/// POST /users/{id}/reinvite — a fresh link for a pending user.
pub async fn reinvite(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    require_owner(&u)?;
    let row = app.db.row("SELECT email, password FROM users WHERE id = ?1", p![&id])?.ok_or_else(|| err(StatusCode::NOT_FOUND, "User not found"))?;
    if !row[1].s().is_empty() {
        return Err(err(StatusCode::BAD_REQUEST, "This user has already accepted the invitation"));
    }
    let token = random_token();
    app.db.exec("UPDATE users SET invite_token = ?2, invite_expires = ?3 WHERE id = ?1", p![&id, sha256_hex(&token), now_ms() + INVITE_DAYS * 86_400_000])?;
    let url = format!("{}/app/#/signup?token={token}", app.cfg.base_url);
    let sent = send_mail(&row[0].s(), "Your Flowwolf invitation", mail_html("Join Flowwolf", "Here is a new link to join.", &url, "Accept invitation")).await;
    ok(json!({"inviteAcceptUrl": url, "emailSent": sent}))
}

fn invited(app: &AppState, token: &str) -> Result<(String, String, String), ApiErr> {
    let row = app.db.row("SELECT id, email, invited_by FROM users WHERE invite_token = ?1 AND invite_expires > ?2 AND password = ''", p![sha256_hex(token), now_ms()])?
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "This invitation link is invalid or has expired"))?;
    Ok((row[0].s(), row[1].s(), row[2].s()))
}

/// GET /resolve-signup-token?token= — who invited whom (public).
pub async fn resolve_signup(State(app): State<AppState>, Query(q): Query<HashMap<String, String>>) -> R {
    let (_, email, by) = invited(&app, q.get("token").map(String::as_str).unwrap_or(""))?;
    let inviter = app.db.row("SELECT first_name, last_name FROM users WHERE id = ?1", p![by])?;
    ok(json!({"email": email, "inviter": {"firstName": inviter.as_ref().map(|r| r[0].s()), "lastName": inviter.as_ref().map(|r| r[1].s())}}))
}

/// POST /invitations/accept (or /invitations/{id}/accept) — sets name and password, then logs in.
pub async fn accept(State(app): State<AppState>, Json(b): Json<Value>) -> Result<Response, ApiErr> {
    let (id, _, _) = invited(&app, &as_str(&b["token"]))?;
    let pw = as_str(&b["password"]);
    check_password(&pw)?;
    app.db.exec("UPDATE users SET password = ?2, first_name = ?3, last_name = ?4, invite_token = NULL, invite_expires = NULL WHERE id = ?1",
                p![&id, hash_password(&pw)?, as_str(&b["firstName"]), as_str(&b["lastName"])])?;
    let u = app.db.row(&format!("SELECT {USER_COLS} FROM users u WHERE u.id = ?1"), p![&id])?.map(|r| user_row(&r)).ok_or_else(|| err(StatusCode::NOT_FOUND, "User not found"))?;
    session_response(&app, u)
}

pub async fn accept_by_id(State(app): State<AppState>, Path(_id): Path<String>, body: Json<Value>) -> Result<Response, ApiErr> {
    accept(State(app), body).await
}

// ---------------------------------------------------------------------------
// roles and deleting users
// ---------------------------------------------------------------------------

/// PATCH /users/{id}/role {newRoleName} — the owner's role never changes, nobody changes their own.
pub async fn change_role(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    require_owner(&u)?;
    if id == u.id {
        return Err(err(StatusCode::BAD_REQUEST, "You cannot change your own role"));
    }
    let role = role_name(&b["newRoleName"])?;
    let current = app.db.row("SELECT role FROM users WHERE id = ?1", p![&id])?.ok_or_else(|| err(StatusCode::NOT_FOUND, "User not found"))?[0].s();
    if current == "owner" {
        return Err(err(StatusCode::FORBIDDEN, "The owner's role cannot be changed"));
    }
    app.db.exec("UPDATE users SET role = ?2 WHERE id = ?1", p![&id, role])?;
    ok(json!({"success": true}))
}

/// DELETE /users/{id}?transferId= — hands workflows and credentials to another user, or deletes them.
pub async fn delete_user(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Query(q): Query<HashMap<String, String>>) -> R {
    require_owner(&u)?;
    if id == u.id {
        return Err(err(StatusCode::BAD_REQUEST, "You cannot delete yourself"));
    }
    let role = app.db.row("SELECT role FROM users WHERE id = ?1", p![&id])?.ok_or_else(|| err(StatusCode::NOT_FOUND, "User not found"))?[0].s();
    if role == "owner" {
        return Err(err(StatusCode::FORBIDDEN, "The owner cannot be deleted"));
    }
    match q.get("transferId").filter(|t| !t.is_empty()) {
        Some(to) => {
            if *to == id || app.db.row("SELECT 1 FROM users WHERE id = ?1 AND password != ''", p![to])?.is_none() {
                return Err(err(StatusCode::BAD_REQUEST, "Transfer the data to another active user"));
            }
            for t in ["workflows", "credentials"] {
                app.db.exec(&format!("UPDATE {t} SET owner_id = ?2 WHERE owner_id = ?1"), p![&id, to])?;
                // The new owner no longer needs a share on what they now own.
                app.db.exec(&format!("DELETE FROM shares WHERE resource = '{t}' AND user_id = ?1 AND resource_id IN (SELECT id FROM {t} WHERE owner_id = ?1)"), p![to])?;
            }
        }
        None => {
            for r in app.db.rows("SELECT id FROM workflows WHERE owner_id = ?1", p![&id])? {
                let wid = r[0].s();
                crate::triggers::deactivate(&app, &wid);
                if let Some(mut w) = app.db.workflow(&wid)? {
                    crate::hooktriggers::unregister(&app, &mut w).await;
                }
                app.db.delete_workflow(&wid)?;
            }
            app.db.exec("DELETE FROM shares WHERE resource = 'credentials' AND resource_id IN (SELECT id FROM credentials WHERE owner_id = ?1)", p![&id])?;
            app.db.exec("DELETE FROM credentials WHERE owner_id = ?1", p![&id])?;
        }
    }
    app.db.exec("DELETE FROM sessions WHERE user_id = ?1", p![&id])?;
    app.db.exec("DELETE FROM api_keys WHERE user_id = ?1", p![&id])?;
    app.db.exec("DELETE FROM shares WHERE user_id = ?1", p![&id])?;
    app.db.exec("DELETE FROM users WHERE id = ?1", p![&id])?;
    ok(json!({"success": true}))
}

// ---------------------------------------------------------------------------
// password reset
// ---------------------------------------------------------------------------

fn new_reset_link(app: &AppState, id: &str) -> Result<String, ApiErr> {
    let token = random_token();
    app.db.exec("UPDATE users SET reset_token = ?2, reset_expires = ?3 WHERE id = ?1", p![id, sha256_hex(&token), now_ms() + RESET_MINUTES * 60_000])?;
    Ok(format!("{}/app/#/change-password?token={token}", app.cfg.base_url))
}

/// POST /forgot-password {email} — always answers the same, so addresses cannot be probed.
pub async fn forgot(State(app): State<AppState>, Json(b): Json<Value>) -> R {
    let email = as_str(&b["email"]).trim().to_lowercase();
    if let Some(r) = app.db.row("SELECT id FROM users WHERE email = ?1 AND password != ''", p![&email])? {
        let link = new_reset_link(&app, &r[0].s())?;
        send_mail(&email, "Reset your Flowwolf password",
                  mail_html("Reset your password", &format!("Use this link within {RESET_MINUTES} minutes to choose a new password."), &link, "Choose a new password")).await;
    } else {
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    ok(json!({"success": true}))
}

/// GET /users/{id}/password-reset-link — for owners and admins to hand out themselves.
pub async fn reset_link(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    require_owner(&u)?;
    let role = app.db.row("SELECT role FROM users WHERE id = ?1 AND password != ''", p![&id])?.ok_or_else(|| err(StatusCode::NOT_FOUND, "User not found"))?[0].s();
    if role == "owner" && u.role != "owner" {
        return Err(err(StatusCode::FORBIDDEN, "Only the owner can reset the owner's password"));
    }
    ok(json!({"link": new_reset_link(&app, &id)?}))
}

fn reset_user(app: &AppState, token: &str) -> Result<(String, bool, Option<String>), ApiErr> {
    let r = app.db.row("SELECT id, mfa_enabled, mfa_secret FROM users WHERE reset_token = ?1 AND reset_expires > ?2", p![sha256_hex(token), now_ms()])?
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "This password reset link is invalid or has expired"))?;
    Ok((r[0].s(), r[1].i() == 1, r[2].os()))
}

/// GET /resolve-password-token?token= (public); tells the page whether a 2FA code is needed.
pub async fn resolve_reset(State(app): State<AppState>, Query(q): Query<HashMap<String, String>>) -> R {
    let (_, mfa, _) = reset_user(&app, q.get("token").map(String::as_str).unwrap_or(""))?;
    ok(json!({"mfaEnabled": mfa}))
}

/// POST /change-password {token, password, mfaCode} — also signs out every session of the user.
pub async fn change_password(State(app): State<AppState>, Json(b): Json<Value>) -> R {
    let (id, mfa, secret) = reset_user(&app, &as_str(&b["token"]))?;
    if mfa && !secret.is_some_and(|s| totp_ok(&s, &as_str(&b["mfaCode"]))) {
        return Err(err(StatusCode::BAD_REQUEST, "Two-factor code is wrong"));
    }
    let pw = as_str(&b["password"]);
    check_password(&pw)?;
    app.db.exec("UPDATE users SET password = ?2, reset_token = NULL, reset_expires = NULL WHERE id = ?1", p![&id, hash_password(&pw)?])?;
    app.db.exec("DELETE FROM sessions WHERE user_id = ?1", p![&id])?;
    ok(json!({"success": true}))
}

// ---------------------------------------------------------------------------
// two-factor authentication
// ---------------------------------------------------------------------------

fn base32(bytes: &[u8]) -> String {
    const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    let mut out = String::new();
    let (mut buf, mut bits) = (0u32, 0);
    for b in bytes {
        buf = (buf << 8) | *b as u32;
        bits += 8;
        while bits >= 5 {
            out.push(A[((buf >> (bits - 5)) & 31) as usize] as char);
            bits -= 5;
        }
    }
    if bits > 0 {
        out.push(A[((buf << (5 - bits)) & 31) as usize] as char);
    }
    out
}

/// Accepts the current code and its neighbours (30 s clock drift either way).
pub fn totp_ok(secret_b32: &str, code: &str) -> bool {
    let code = code.trim().replace(' ', "");
    let Ok(secret) = crate::nodes::base32_decode(secret_b32) else { return false };
    let now = chrono::Utc::now().timestamp();
    code.len() == 6 && [-30, 0, 30].iter().any(|d| crate::nodes::totp_at(&secret, now + d, 30, 6, "SHA1").is_ok_and(|c| c == code))
}

/// GET /mfa/qr — a new secret (kept until enabled) with otpauth link, QR code and recovery codes.
pub async fn mfa_qr(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    let row = app.db.row("SELECT mfa_enabled FROM users WHERE id = ?1", p![&u.id])?;
    if row.is_some_and(|r| r[0].i() == 1) {
        return Err(err(StatusCode::BAD_REQUEST, "Two-factor authentication is already on"));
    }
    let secret = base32(&rand_bytes(20));
    let codes: Vec<String> = (0..10).map(|_| { let t = random_token(); format!("{}-{}", &t[..5], &t[5..10]).to_uppercase() }).collect();
    let hashed: Vec<String> = codes.iter().map(|c| sha256_hex(c)).collect();
    app.db.exec("UPDATE users SET mfa_secret = ?2, mfa_recovery = ?3 WHERE id = ?1", p![&u.id, &secret, json!(hashed).to_string()])?;
    let url = format!("otpauth://totp/Flowwolf:{}?secret={secret}&issuer=Flowwolf", u.email.replace('@', "%40"));
    let qr = qrcode::QrCode::new(url.as_bytes()).map(|q| q.render::<qrcode::render::svg::Color>().min_dimensions(200, 200).build()).unwrap_or_default();
    ok(json!({"secret": secret, "qrCode": url, "qrSvg": qr, "recoveryCodes": codes}))
}

fn rand_bytes(n: usize) -> Vec<u8> {
    use aes_gcm::aead::OsRng;
    use aes_gcm::aead::rand_core::RngCore;
    let mut v = vec![0u8; n];
    OsRng.fill_bytes(&mut v);
    v
}

/// POST /mfa/enable {mfaCode} — turns 2FA on once a code from the new secret is shown.
pub async fn mfa_enable(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let secret = app.db.row("SELECT mfa_secret FROM users WHERE id = ?1", p![&u.id])?.and_then(|r| r[0].os())
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "Request a QR code first"))?;
    if !totp_ok(&secret, &as_str(&b["mfaCode"])) {
        return Err(err(StatusCode::BAD_REQUEST, "The code is wrong; check the time on your device"));
    }
    app.db.exec("UPDATE users SET mfa_enabled = 1 WHERE id = ?1", p![&u.id])?;
    ok(json!({"success": true}))
}

/// POST /mfa/disable {mfaCode | mfaRecoveryCode}
pub async fn mfa_disable(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    if second_factor(&app, &u.id, &b)? != Some(true) {
        return Err(err(StatusCode::BAD_REQUEST, "A valid two-factor or recovery code is needed"));
    }
    app.db.exec("UPDATE users SET mfa_enabled = 0, mfa_secret = NULL, mfa_recovery = NULL WHERE id = ?1", p![&u.id])?;
    ok(json!({"success": true}))
}

/// None when the user has no 2FA; otherwise whether the code (or an unused recovery code) fits.
/// Recovery codes work once.
pub fn second_factor(app: &AppState, id: &str, b: &Value) -> Result<Option<bool>, ApiErr> {
    let Some(r) = app.db.row("SELECT mfa_enabled, mfa_secret, mfa_recovery FROM users WHERE id = ?1", p![id])? else { return Ok(None) };
    if r[0].i() != 1 {
        return Ok(None);
    }
    if let Some(code) = b["mfaCode"].as_str().filter(|c| !c.is_empty()) {
        return Ok(Some(r[1].os().is_some_and(|s| totp_ok(&s, code))));
    }
    if let Some(code) = b["mfaRecoveryCode"].as_str().filter(|c| !c.is_empty()) {
        let mut left: Vec<String> = serde_json::from_str(&r[2].s()).unwrap_or_default();
        let h = sha256_hex(&code.trim().to_uppercase());
        if let Some(pos) = left.iter().position(|x| *x == h) {
            left.remove(pos);
            app.db.exec("UPDATE users SET mfa_recovery = ?2 WHERE id = ?1", p![id, json!(left).to_string()])?;
            return Ok(Some(true));
        }
        return Ok(Some(false));
    }
    Ok(Some(false))
}

/// The answer n8n gives when the password is right but the second factor is missing or wrong.
pub fn mfa_required() -> Response {
    (StatusCode::UNAUTHORIZED, Json(json!({"code": 998, "message": "MFA Error", "mfaRequired": true}))).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base32_round_trip_and_codes() {
        let raw = b"12345678901234567890";
        let s = base32(raw);
        assert_eq!(s, "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ");
        assert_eq!(crate::nodes::base32_decode(&s).unwrap(), raw);
        let now = chrono::Utc::now().timestamp();
        let code = crate::nodes::totp_at(raw, now, 30, 6, "SHA1").unwrap();
        assert!(totp_ok(&s, &code));
        assert!(!totp_ok(&s, "000000") || code == "000000");
        assert_eq!(role_name(&json!("global:admin")).unwrap(), "admin");
        assert!(role_name(&json!("owner")).is_err());
    }
}
