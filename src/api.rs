//! HTTP layer: REST API for the editor, public API (/api/v1), webhooks, forms and static files.

use crate::app::{AppState, TestHook};
use crate::engine::{Launch, Reply, StartSpec, launch};
use crate::model::*;
use crate::security::{hash_password, random_token, sha256_hex, verify_password};
use crate::triggers;
use axum::body::{Body, Bytes};
use axum::extract::{Path, Query, Request, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{any, get, patch, post};
use axum::{Extension, Json, Router};
use crate::p;
use rust_embed::RustEmbed;
use serde_json::{Map, Value, json};
use std::collections::HashMap;
use std::convert::Infallible;
use futures::StreamExt;
use std::time::Duration;
use tokio::sync::{mpsc, oneshot};

#[derive(RustEmbed)]
#[folder = "web/"]
struct Assets;

const MASK: &str = "__FLOWWOLF_MASKED__";
const SESSION_DAYS: i64 = 30;

// ---------------------------------------------------------------------------
// errors & helpers
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub struct ApiErr(pub(crate) StatusCode, pub(crate) String);

impl IntoResponse for ApiErr {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"message": self.1, "code": self.0.as_u16()}))).into_response()
    }
}

impl<E: Into<anyhow::Error>> From<E> for ApiErr {
    fn from(e: E) -> Self {
        ApiErr(StatusCode::BAD_REQUEST, e.into().to_string())
    }
}

pub(crate) type R = Result<Json<Value>, ApiErr>;

pub(crate) fn ok(v: impl serde::Serialize) -> R {
    Ok(Json(json!({ "data": v })))
}

pub(crate) fn err(code: StatusCode, msg: impl Into<String>) -> ApiErr {
    ApiErr(code, msg.into())
}

fn not_found(what: &str) -> ApiErr {
    err(StatusCode::NOT_FOUND, format!("{what} not found"))
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct User {
    pub(crate) id: String,
    pub(crate) email: String,
    pub(crate) first_name: String,
    pub(crate) last_name: String,
    pub(crate) role: String,
}

pub(crate) fn user_row(r: &crate::db::Row) -> User {
    User { id: r[0].s(), email: r[1].s(), first_name: r[2].s(), last_name: r[3].s(), role: r[4].s() }
}

pub(crate) const USER_COLS: &str = "u.id, u.email, u.first_name, u.last_name, u.role";

fn cookie(headers: &HeaderMap, name: &str) -> Option<String> {
    headers.get_all(header::COOKIE).iter().filter_map(|v| v.to_str().ok()).flat_map(|v| v.split(';')).find_map(|c| {
        let (k, v) = c.trim().split_once('=')?;
        (k == name).then(|| v.to_string())
    })
}

fn current_user(app: &AppState, headers: &HeaderMap) -> Option<User> {
    if let Some(key) = headers.get("x-n8n-api-key").or_else(|| headers.get("x-flowwolf-api-key")).and_then(|v| v.to_str().ok()) {
        let h = sha256_hex(key);
        return app.db.row(&format!("SELECT {USER_COLS} FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?1"), p![h]).ok().flatten().map(|r| user_row(&r));
    }
    let token = cookie(headers, "fw_session")?;
    app.db
        .row(&format!("SELECT {USER_COLS} FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?1 AND s.expires_at > ?2"), p![sha256_hex(&token), now_ms()])
        .ok()
        .flatten()
        .map(|r| user_row(&r))
}

async fn auth(State(app): State<AppState>, mut req: Request, next: Next) -> Response {
    match current_user(&app, req.headers()) {
        Some(u) => {
            req.extensions_mut().insert(u);
            next.run(req).await
        }
        None => err(StatusCode::UNAUTHORIZED, "Unauthorized").into_response(),
    }
}

/// The public API returns single objects unwrapped, like n8n.
async fn public_api_shape(req: Request, next: Next) -> Response {
    let res = next.run(req).await;
    let is_json = res.headers().get(header::CONTENT_TYPE).is_some_and(|v| v.as_bytes().starts_with(b"application/json"));
    if !is_json {
        return res;
    }
    let (parts, body) = res.into_parts();
    let Ok(bytes) = axum::body::to_bytes(body, 64 * 1024 * 1024).await else { return StatusCode::INTERNAL_SERVER_ERROR.into_response() };
    let v: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    let out = match v.get("data") {
        Some(Value::Array(a)) => json!({"data": a, "nextCursor": null}),
        Some(d) => d.clone(),
        None => v,
    };
    let mut res = Response::from_parts(parts, Body::from(out.to_string()));
    res.headers_mut().remove(header::CONTENT_LENGTH);
    res
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------

pub fn router(app: AppState) -> Router {
    let protected = Router::new()
        .route("/login", get(me))
        .route("/logout", post(logout))
        .route("/me", patch(update_me))
        .route("/users", get(crate::users::list_users).post(create_user))
        .route("/users/{id}", axum::routing::delete(crate::users::delete_user))
        .route("/users/{id}/role", patch(crate::users::change_role))
        .route("/users/{id}/reinvite", post(crate::users::reinvite))
        .route("/users/{id}/password-reset-link", get(crate::users::reset_link))
        .route("/invitations", post(crate::users::invite))
        .route("/updates", get(crate::updates::get))
        .route("/updates/check", post(crate::updates::check_now))
        .route("/updates/install", post(crate::updates::install))
        .route("/mfa/qr", get(crate::users::mfa_qr))
        .route("/mfa/enable", post(crate::users::mfa_enable))
        .route("/mfa/disable", post(crate::users::mfa_disable))
        .route("/workflows", get(list_workflows).post(create_workflow))
        .route("/workflows/run", post(run_workflow))
        .route("/workflows/listen", post(listen))
        .route("/workflows/listen/stop", post(stop_listen))
        .route("/workflows/{id}", get(get_workflow).patch(update_workflow).put(update_workflow).delete(delete_workflow))
        .route("/workflows/{id}/activate", post(activate))
        .route("/workflows/{id}/share", get(get_wf_share).put(put_wf_share))
        .route("/credentials/{id}/share", get(get_cred_share).put(put_cred_share))
        .route("/workflows/{id}/deactivate", post(deactivate))
        .route("/executions", get(list_executions))
        .route("/executions/{id}", get(get_execution).delete(delete_execution))
        .route("/executions/{id}/stop", post(stop_execution))
        .route("/executions/{id}/retry", post(retry_execution))
        .route("/credentials", get(list_credentials).post(create_credential))
        .route("/credentials/{id}", get(get_credential).patch(update_credential).delete(delete_credential))
        .route("/node-types", get(|| async { ok(&crate::nodes::descriptions()["nodes"]) }))
        .route("/credential-types", get(|| async { ok(&crate::nodes::descriptions()["credentials"]) }))
        .route("/variables", get(list_variables).post(create_variable))
        .route("/variables/{id}", patch(update_variable).delete(delete_variable))
        .route("/api-keys", get(list_api_keys).post(create_api_key))
        .route("/api-keys/{id}", axum::routing::delete(delete_api_key))
        .route("/push", get(push))
        .route("/source-control/preferences", get(sc_get).put(sc_put))
        .route("/source-control/push", post(sc_push))
        .route("/source-control/pull", post(sc_pull))
        .route("/source-control/branches", get(sc_branches))
        .route("/source-control/generate-key-pair", post(sc_generate_key))
        .route("/oauth2-credential/auth", get(oauth_auth))
        .route_layer(middleware::from_fn_with_state(app.clone(), auth));
    let rest = Router::new()
        .route("/settings", get(settings))
        .route("/owner/setup", post(setup_owner))
        .route("/login", post(login))
        .route("/resolve-signup-token", get(crate::users::resolve_signup))
        .route("/invitations/accept", post(crate::users::accept))
        .route("/invitations/{id}/accept", post(crate::users::accept_by_id))
        .route("/forgot-password", post(crate::users::forgot))
        .route("/resolve-password-token", get(crate::users::resolve_reset))
        .route("/change-password", post(crate::users::change_password))
        .route("/oauth2-credential/callback", get(oauth_callback))
        .merge(protected);

    Router::new()
        .nest("/rest", rest.clone())
        .nest("/api/v1", rest.layer(middleware::from_fn(public_api_shape)))
        .route("/webhook/{*path}", any(prod_webhook))
        .route("/webhook-test/{*path}", any(test_webhook))
        .route("/form/{*path}", get(form_page).post(form_submit))
        .route("/form-test/{*path}", get(form_page_test).post(form_submit_test))
        .route("/chat/{*path}", get(chat_page).post(chat_message))
        .route("/chat-test/{*path}", get(chat_page_test).post(chat_message_test))
        .route("/mcp/{*path}", post(mcp_endpoint).get(mcp_get))
        .route("/webhook-waiting/{id}", any(resume_webhook))
        .route("/form-waiting/{id}", get(resume_form_page).post(resume_form_submit))
        .route("/healthz", get(|| async { Json(json!({"status": "ok"})) }))
        .fallback(get(static_file))
        .with_state(app)
}

// ---------------------------------------------------------------------------
// auth & users
// ---------------------------------------------------------------------------

/// 404 instead of 403 so ids of other users' resources are not confirmed.
fn access(app: &AppState, u: &User, table: &str, id: &str) -> Result<(), ApiErr> {
    if app.db.can_access(table, id, &u.id)? { Ok(()) } else { Err(not_found(if table == "workflows" { "Workflow" } else { "Credential" })) }
}

/// Like `access`, but also requires edit rights (viewers get 403).
fn edit_access(app: &AppState, u: &User, table: &str, id: &str) -> Result<(), ApiErr> {
    access(app, u, table, id)?;
    if !app.db.can_edit(table, id, &u.id)? {
        return Err(err(StatusCode::FORBIDDEN, "You only have view access"));
    }
    Ok(())
}

fn has_owner(app: &AppState) -> bool {
    app.db.row("SELECT COUNT(*) FROM users", &[]).ok().flatten().is_some_and(|r| r[0].i() > 0)
}

async fn settings(State(app): State<AppState>) -> R {
    ok(json!({
        "setupComplete": has_owner(&app),
        "version": env!("CARGO_PKG_VERSION"),
        "webhookUrl": format!("{}/webhook", app.cfg.base_url),
        "webhookTestUrl": format!("{}/webhook-test", app.cfg.base_url),
        "formUrl": format!("{}/form", app.cfg.base_url),
        "timezone": app.cfg.timezone,
        "executeCommandEnabled": app.cfg.enable_execute_command,
    }))
}

pub(crate) fn session_response(app: &AppState, user: User) -> Result<Response, ApiErr> {
    let token = random_token();
    app.db.exec("INSERT INTO sessions(token, user_id, expires_at) VALUES(?1, ?2, ?3)", p![sha256_hex(&token), &user.id, now_ms() + SESSION_DAYS * 86_400_000])?;
    let secure = if app.cfg.base_url.starts_with("https") { "; Secure" } else { "" };
    let c = format!("fw_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={}{secure}", SESSION_DAYS * 86400);
    let mut res = Json(json!({"data": user})).into_response();
    res.headers_mut().insert(header::SET_COOKIE, HeaderValue::from_str(&c).unwrap());
    Ok(res)
}

pub(crate) fn check_password(pw: &str) -> Result<(), ApiErr> {
    if pw.len() < 8 {
        return Err(err(StatusCode::BAD_REQUEST, "Password must be at least 8 characters long"));
    }
    Ok(())
}

async fn setup_owner(State(app): State<AppState>, Json(b): Json<Value>) -> Result<Response, ApiErr> {
    if has_owner(&app) {
        return Err(err(StatusCode::BAD_REQUEST, "Owner is already set up"));
    }
    let email = as_str(&b["email"]).trim().to_lowercase();
    let pw = as_str(&b["password"]);
    if !email.contains('@') {
        return Err(err(StatusCode::BAD_REQUEST, "Invalid email"));
    }
    check_password(&pw)?;
    let user = User { id: short_id(), email, first_name: as_str(&b["firstName"]), last_name: as_str(&b["lastName"]), role: "owner".into() };
    let hash = hash_password(&pw)?;
    app.db.exec(
        "INSERT INTO users(id, email, first_name, last_name, password, role, created_at) VALUES(?1, ?2, ?3, ?4, ?5, 'owner', ?6)",
        p![&user.id, &user.email, &user.first_name, &user.last_name, hash, now_iso()],
    )?;
    app.db.migrate()?;
    session_response(&app, user)
}

async fn login(State(app): State<AppState>, Json(b): Json<Value>) -> Result<Response, ApiErr> {
    let email = as_str(&b["email"]).trim().to_lowercase();
    let row = app.db.row(&format!("SELECT {USER_COLS}, u.password FROM users u WHERE u.email = ?1"), p![&email])?.map(|r| (user_row(&r), r[5].s()));
    match row {
        Some((u, hash)) if !hash.is_empty() && verify_password(&as_str(&b["password"]), &hash) => match crate::users::second_factor(&app, &u.id, &b)? {
            Some(false) => Ok(crate::users::mfa_required()),
            _ => session_response(&app, u),
        },
        _ => {
            tokio::time::sleep(Duration::from_millis(500)).await;
            Err(err(StatusCode::UNAUTHORIZED, "Wrong username or password"))
        }
    }
}

async fn me(Extension(u): Extension<User>) -> R {
    ok(u)
}

async fn logout(State(app): State<AppState>, headers: HeaderMap) -> Result<Response, ApiErr> {
    if let Some(t) = cookie(&headers, "fw_session") {
        app.db.exec("DELETE FROM sessions WHERE token = ?1", p![sha256_hex(&t)])?;
    }
    let mut res = Json(json!({"data": true})).into_response();
    res.headers_mut().insert(header::SET_COOKIE, HeaderValue::from_static("fw_session=; HttpOnly; Path=/; Max-Age=0"));
    Ok(res)
}

async fn update_me(State(app): State<AppState>, Extension(u): Extension<User>, headers: HeaderMap, Json(b): Json<Value>) -> R {
    let current = cookie(&headers, "fw_session").map(|t| sha256_hex(&t));
    if let Some(pw) = b["newPassword"].as_str() {
        let hash = app.db.row("SELECT password FROM users WHERE id = ?1", p![&u.id])?.map(|r| r[0].s()).unwrap_or_default();
        if !verify_password(&as_str(&b["currentPassword"]), &hash) {
            return Err(err(StatusCode::BAD_REQUEST, "Current password is wrong"));
        }
        check_password(pw)?;
        let h = hash_password(pw)?;
        app.db.exec("UPDATE users SET password = ?2 WHERE id = ?1", p![&u.id, h])?;
        app.db.exec("DELETE FROM sessions WHERE user_id = ?1 AND token != ?2", p![&u.id, current.unwrap_or_default()])?;
    }
    if b["firstName"].is_string() || b["lastName"].is_string() {
        app.db.exec("UPDATE users SET first_name = COALESCE(?2, first_name), last_name = COALESCE(?3, last_name) WHERE id = ?1", p![&u.id, b["firstName"].as_str(), b["lastName"].as_str()])?;
    }
    ok(true)
}

pub(crate) fn require_owner(u: &User) -> Result<(), ApiErr> {
    if u.role != "owner" && u.role != "admin" {
        return Err(err(StatusCode::FORBIDDEN, "Only the owner or an admin can manage users"));
    }
    Ok(())
}


async fn create_user(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    require_owner(&u)?;
    let pw = as_str(&b["password"]);
    check_password(&pw)?;
    let role = if b["role"] == "admin" { "admin" } else { "member" };
    let nu = User { id: short_id(), email: as_str(&b["email"]).trim().to_lowercase(), first_name: as_str(&b["firstName"]), last_name: as_str(&b["lastName"]), role: role.into() };
    let h = hash_password(&pw)?;
    app.db
        .exec("INSERT INTO users(id, email, first_name, last_name, password, role, created_at) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7)", p![&nu.id, &nu.email, &nu.first_name, &nu.last_name, h, &nu.role, now_iso()])
        .map_err(|_| err(StatusCode::BAD_REQUEST, "A user with this email already exists"))?;
    ok(nu)
}


// ---------------------------------------------------------------------------
// workflows
// ---------------------------------------------------------------------------

fn summary(w: &Workflow) -> Value {
    json!({"id": w.id, "name": w.name, "active": w.active, "tags": w.tags, "folder": w.folder, "createdAt": w.created_at, "updatedAt": w.updated_at,
           "nodeTypes": w.nodes.iter().map(|n| n.kind.clone()).collect::<std::collections::BTreeSet<_>>()})
}

async fn list_workflows(State(app): State<AppState>, Extension(u): Extension<User>, Query(q): Query<HashMap<String, String>>) -> R {
    let visible = app.db.visible_ids("workflows", &u.id)?;
    let all: Vec<Workflow> = app.db.workflows()?.into_iter().filter(|w| visible.as_ref().is_none_or(|v| v.contains(&w.id))).collect();
    let active = q.get("active").map(|a| a == "true");
    let full = q.get("full").is_some_and(|v| v == "true");
    let list: Vec<Value> = all
        .iter()
        .filter(|w| active.is_none_or(|a| w.active == a))
        .map(|w| if full { serde_json::to_value(w).unwrap() } else { summary(w) })
        .collect();
    ok(list)
}

/// Fills ids that n8n would generate (node ids, webhook ids).
fn normalize(w: &mut Workflow) {
    for n in w.nodes.iter_mut() {
        if n.id.is_empty() {
            n.id = uuid::Uuid::new_v4().to_string();
        }
        if triggers::is_hook_node(n) && n.webhook_id.is_none() {
            n.webhook_id = Some(uuid::Uuid::new_v4().to_string());
        }
    }
    if !w.connections.is_object() {
        w.connections = json!({});
    }
    if !w.settings.is_object() {
        w.settings = json!({"executionOrder": "v1"});
    }
}

async fn create_workflow(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let mut w: Workflow = serde_json::from_value(b)?;
    if w.id.is_empty() || app.db.workflow(&w.id)?.is_some() {
        w.id = short_id();
    }
    if w.name.trim().is_empty() {
        w.name = "My workflow".into();
    }
    w.active = false;
    w.created_at = now_iso();
    w.updated_at = w.created_at.clone();
    normalize(&mut w);
    app.db.save_workflow(&w)?;
    app.db.set_owner("workflows", &w.id, &u.id)?;
    ok(w)
}

async fn get_workflow(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    access(&app, &u, "workflows", &id)?;
    let mut w = serde_json::to_value(app.db.workflow(&id)?.ok_or_else(|| not_found("Workflow"))?)?;
    w["canEdit"] = json!(app.db.can_edit("workflows", &id, &u.id)?);
    ok(w)
}

async fn update_workflow(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    edit_access(&app, &u, "workflows", &id)?;
    let old = app.db.workflow(&id)?.ok_or_else(|| not_found("Workflow"))?;
    let mut merged = serde_json::to_value(&old)?;
    for (k, v) in b.as_object().cloned().unwrap_or_default() {
        merged[k] = v;
    }
    let mut w: Workflow = serde_json::from_value(merged)?;
    w.id = id;
    w.created_at = old.created_at.clone();
    w.updated_at = now_iso();
    normalize(&mut w);
    // Registered webhooks live in the stored static data, not in the submitted body.
    w.static_data = old.static_data.clone();
    let changed = crate::hooktriggers::fingerprint(&w) != crate::hooktriggers::fingerprint(&old);
    if old.active && (!w.active || changed) {
        let mut prev = old.clone();
        crate::hooktriggers::unregister(&app, &mut prev).await;
        w.static_data = prev.static_data;
    }
    if w.active {
        let registered = match triggers::activate(&app, &w) {
            Ok(()) => crate::hooktriggers::register(&app, &mut w).await,
            Err(e) => Err(e),
        };
        if let Err(e) = registered {
            w.active = false;
            app.db.save_workflow(&w)?;
            triggers::deactivate(&app, &w.id);
            return Err(err(StatusCode::BAD_REQUEST, format!("Workflow saved but could not be activated: {e}")));
        }
    } else if old.active {
        triggers::deactivate(&app, &w.id);
    }
    app.db.save_workflow(&w)?;
    if w.active {
        triggers::fire_workflow_trigger(&app, &w, if old.active { "update" } else { "activate" });
    }
    ok(w)
}

async fn set_active(app: &AppState, id: &str, active: bool) -> R {
    let mut w = app.db.workflow(id)?.ok_or_else(|| not_found("Workflow"))?;
    let was_active = w.active;
    if active {
        triggers::activate(app, &w).map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?;
        if let Err(e) = crate::hooktriggers::register(app, &mut w).await {
            triggers::deactivate(app, id);
            app.db.save_static_data(id, &w.static_data)?;
            return Err(err(StatusCode::BAD_REQUEST, e.to_string()));
        }
    } else {
        triggers::deactivate(app, id);
        crate::hooktriggers::unregister(app, &mut w).await;
    }
    w.active = active;
    app.db.save_workflow(&w)?;
    if active && !was_active {
        triggers::fire_workflow_trigger(app, &w, "activate");
    }
    ok(w)
}

async fn activate(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    edit_access(&app, &u, "workflows", &id)?;
    set_active(&app, &id, true).await
}

async fn deactivate(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    edit_access(&app, &u, "workflows", &id)?;
    set_active(&app, &id, false).await
}

async fn delete_workflow(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    if !app.db.can_manage("workflows", &id, &u.id)? {
        return Err(err(StatusCode::FORBIDDEN, "Only the owner of a workflow can delete it"));
    }
    triggers::deactivate(&app, &id);
    if let Some(mut w) = app.db.workflow(&id)? {
        crate::hooktriggers::unregister(&app, &mut w).await;
    }
    app.db.delete_workflow(&id)?;
    ok(true)
}

async fn run_workflow(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let wf: Workflow = serde_json::from_value(b["workflowData"].clone())?;
    if !wf.id.is_empty() && app.db.workflow(&wf.id)?.is_some() {
        edit_access(&app, &u, "workflows", &wf.id)?;
    }
    let spec = StartSpec { destination: b["destinationNode"].as_str().map(String::from), ..Default::default() };
    let (id, _) = launch(app, Launch { wf, mode: "manual".into(), spec, reply: None, retry_of: None, user: Some(u.id), exec_id: None })?;
    ok(json!({"executionId": id.to_string()}))
}

/// Registers test webhooks / forms of an unsaved workflow and waits for the first call.
async fn listen(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let mut wf: Workflow = serde_json::from_value(b["workflowData"].clone())?;
    if !wf.id.is_empty() && app.db.workflow(&wf.id)?.is_some() {
        edit_access(&app, &u, "workflows", &wf.id)?;
    }
    normalize(&mut wf);
    let (tx, mut rx) = mpsc::channel::<i64>(1);
    let mut keys = vec![];
    {
        let mut th = app.test_hooks.lock().unwrap();
        th.retain(|_, h| h.wf.id != wf.id || wf.id.is_empty());
        for n in wf.nodes.iter().filter(|n| triggers::is_hook_node(n)) {
            let key = (triggers::hook_method(n), triggers::hook_path(n));
            keys.push(json!({"method": key.0, "path": key.1, "node": n.name}));
            th.insert(key, TestHook { wf: wf.clone(), node: n.name.clone(), notify: tx.clone() });
        }
    }
    if keys.is_empty() {
        return Err(err(StatusCode::BAD_REQUEST, "The workflow has no Webhook or Form trigger to listen on"));
    }
    drop(tx);
    let got = tokio::time::timeout(Duration::from_secs(120), rx.recv()).await.ok().flatten();
    // Chat test URLs stay registered for follow-up messages until "Stop listening".
    app.test_hooks.lock().unwrap().retain(|(m, _), h| !(keys.iter().any(|k| k["node"] == h.node.as_str() && h.wf.id == wf.id) && (m != "CHAT" || got.is_none())));
    ok(json!({"executionId": got.map(|i| i.to_string()), "webhooks": keys}))
}

async fn stop_listen(State(app): State<AppState>, Json(b): Json<Value>) -> R {
    let id = as_str(&b["workflowId"]);
    app.test_hooks.lock().unwrap().retain(|_, h| h.wf.id != id);
    ok(true)
}

// ---------------------------------------------------------------------------
// source control (Git)
// ---------------------------------------------------------------------------

async fn sc_get(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    let mut p: Value = app.db.setting(crate::gitsync::PREFS_KEY)?.and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}));
    p["tokenSet"] = json!(app.db.setting(crate::gitsync::TOKEN_KEY)?.is_some());
    ok(p)
}

async fn sc_put(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    require_owner(&u)?;
    let old: Value = app.db.setting(crate::gitsync::PREFS_KEY)?.and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}));
    let url = as_str(&b["repositoryUrl"]).trim().to_string();
    // A different server (or an explicit reset) forgets the pinned SSH host key.
    let keep_host = b["resetHostKey"] != true && as_str(&old["repositoryUrl"]) == url;
    let prefs = json!({
        "provider": match as_str(&b["provider"]).as_str() { "gitea" => "gitea", "ssh" => "ssh", _ => "github" },
        "apiUrl": as_str(&b["apiUrl"]).trim(),
        "repository": as_str(&b["repository"]).trim(),
        "repositoryUrl": url,
        "branch": as_str(&b["branch"]).trim(),
        "folder": as_str(&b["folder"]).trim(),
        "publicKey": old["publicKey"],
        "hostKey": if keep_host { old["hostKey"].clone() } else { Value::Null },
    });
    app.db.set_setting(crate::gitsync::PREFS_KEY, &prefs.to_string())?;
    if let Some(t) = b["token"].as_str().filter(|t| !t.is_empty()) {
        app.db.set_setting(crate::gitsync::TOKEN_KEY, &app.cipher.encrypt(&json!(t))?)?;
    }
    sc_get(State(app), Extension(u)).await
}

async fn sc_push(State(app): State<AppState>, Extension(u): Extension<User>, body: Option<Json<Value>>) -> R {
    require_owner(&u)?;
    let msg = body.as_ref().map(|b| as_str(&b["message"])).unwrap_or_default();
    ok(crate::gitsync::push(&app, &msg).await.map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?)
}

async fn sc_generate_key(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    let public = crate::gitsync::generate_key(&app).map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?;
    ok(json!({"publicKey": public}))
}

async fn sc_branches(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    ok(crate::gitsync::branches(&app).await.map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?)
}

async fn sc_pull(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    ok(crate::gitsync::pull(&app, &u.id).await.map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?)
}

// ---------------------------------------------------------------------------
// sharing
// ---------------------------------------------------------------------------

fn get_share(app: &AppState, u: &User, table: &str, id: &str) -> R {
    access(app, u, table, id)?;
    let shares = app.db.shared_with(table, id)?;
    ok(json!({
        "ownerId": app.db.owner_of(table, id)?,
        "userIds": shares.iter().map(|s| &s.0).collect::<Vec<_>>(),
        "shares": shares.iter().map(|(u, r)| json!({"userId": u, "role": r})).collect::<Vec<_>>(),
        "canManage": app.db.can_manage(table, id, &u.id)?,
    }))
}

fn put_share(app: &AppState, u: &User, table: &str, id: &str, b: &Value) -> R {
    access(app, u, table, id)?;
    if !app.db.can_manage(table, id, &u.id)? {
        return Err(err(StatusCode::FORBIDDEN, "Only the owner can change sharing"));
    }
    let owner = app.db.owner_of(table, id)?;
    // {shares: [{userId, role}]} or the older {userIds: [...]} (editor role).
    let users: Vec<(String, String)> = match b["shares"].as_array() {
        Some(list) => list.iter().map(|s| (as_str(&s["userId"]), s["role"].as_str().unwrap_or("editor").to_string())).collect(),
        None => b["userIds"].as_array().into_iter().flatten().map(|x| (as_str(x), "editor".to_string())).collect(),
    };
    let users: Vec<(String, String)> = users.into_iter().filter(|(x, _)| Some(x) != owner.as_ref()).collect();
    app.db.set_shares(table, id, &users)?;
    get_share(app, u, table, id)
}

async fn get_wf_share(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    get_share(&app, &u, "workflows", &id)
}
async fn put_wf_share(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    put_share(&app, &u, "workflows", &id, &b)
}
async fn get_cred_share(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    get_share(&app, &u, "credentials", &id)
}
async fn put_cred_share(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    put_share(&app, &u, "credentials", &id, &b)
}

// ---------------------------------------------------------------------------
// executions
// ---------------------------------------------------------------------------

async fn list_executions(State(app): State<AppState>, Extension(u): Extension<User>, Query(q): Query<HashMap<String, String>>) -> R {
    let limit = q.get("limit").and_then(|l| l.parse().ok()).unwrap_or(50).clamp(1, 250);
    let before = q.get("lastId").or(q.get("cursor")).and_then(|l| l.parse().ok());
    let visible = app.db.visible_ids("workflows", &u.id)?;
    // ponytail: filtered after the query, so members may get fewer rows than `limit` per page.
    let list: Vec<Value> = app
        .db
        .executions(q.get("workflowId").map(|s| s.as_str()), q.get("status").map(|s| s.as_str()), limit, before)?
        .into_iter()
        .filter(|e| visible.as_ref().is_none_or(|v| v.contains(&as_str(&e["workflowId"]))))
        .collect();
    ok(list)
}

fn exec_access(app: &AppState, u: &User, id: i64) -> Result<Value, ApiErr> {
    let ex = app.db.execution(id)?.ok_or_else(|| not_found("Execution"))?;
    if !app.db.can_access("workflows", &as_str(&ex["workflowId"]), &u.id)? {
        return Err(not_found("Execution"));
    }
    Ok(ex)
}

fn exec_edit(app: &AppState, u: &User, id: i64) -> Result<Value, ApiErr> {
    let ex = exec_access(app, u, id)?;
    if !app.db.can_edit("workflows", &as_str(&ex["workflowId"]), &u.id)? {
        return Err(err(StatusCode::FORBIDDEN, "You only have view access"));
    }
    Ok(ex)
}

fn exec_id(id: &str) -> Result<i64, ApiErr> {
    id.parse().map_err(|_| err(StatusCode::BAD_REQUEST, "Invalid execution id"))
}

async fn get_execution(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    ok(exec_access(&app, &u, exec_id(&id)?)?)
}

async fn delete_execution(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    exec_edit(&app, &u, exec_id(&id)?)?;
    app.db.delete_execution(exec_id(&id)?)?;
    ok(true)
}

async fn stop_execution(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    let id = exec_id(&id)?;
    exec_edit(&app, &u, id)?;
    let running = app.running.lock().unwrap().get(&id).cloned();
    match running {
        Some(flag) => flag.store(true, std::sync::atomic::Ordering::Relaxed),
        None if app.db.take_waiting(id)?.is_some() => app.db.set_status(id, "canceled")?,
        None if app.db.status_of(id)?.as_deref() == Some("queued") => app.db.set_status(id, "canceled")?,
        // Running on a worker: it watches for this status.
        None if app.db.status_of(id)?.as_deref() == Some("running") && app.cfg.queue_mode => app.db.set_status(id, "canceling")?,
        None => return Err(err(StatusCode::BAD_REQUEST, "Execution is not running")),
    }
    ok(json!({"stopped": true}))
}

async fn retry_execution(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, body: Option<Json<Value>>) -> R {
    let id = exec_id(&id)?;
    let ex = exec_edit(&app, &u, id)?;
    let use_current = body.as_ref().is_some_and(|b| b["loadWorkflow"] == true);
    let old: Workflow = serde_json::from_value(ex["workflowData"].clone())?;
    let wf = if use_current { app.db.workflow(&old.id)?.unwrap_or(old.clone()) } else { old.clone() };
    // Replay the trigger output of the original run.
    let run_data = &ex["data"]["resultData"]["runData"];
    let start = old.nodes.iter().find(|n| crate::nodes::is_trigger(&n.kind) && run_data.get(&n.name).is_some());
    let spec = match start {
        Some(n) => {
            let items = run_data[&n.name][0]["data"]["main"][0].clone();
            StartSpec { start_node: Some(n.name.clone()), items: Some(to_items(items)), destination: None, ..Default::default() }
        }
        None => StartSpec::default(),
    };
    let (nid, rx) = launch(app, Launch { wf, mode: "retry".into(), spec, reply: None, retry_of: Some(id), user: None, exec_id: None })?;
    let fin = rx.await.map_err(|_| err(StatusCode::INTERNAL_SERVER_ERROR, "execution lost"))?;
    ok(json!({"executionId": nid.to_string(), "status": fin.status}))
}

// ---------------------------------------------------------------------------
// credentials
// ---------------------------------------------------------------------------

fn secret_fields(kind: &str) -> Vec<String> {
    crate::nodes::descriptions()["credentials"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|c| c["name"] == kind)
        .flat_map(|c| c["properties"].as_array().cloned().unwrap_or_default())
        .filter(|p| p["typeOptions"]["password"] == true)
        .map(|p| as_str(&p["name"]))
        .collect()
}

async fn list_credentials(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    let visible = app.db.visible_ids("credentials", &u.id)?;
    ok(app.db.credentials()?.into_iter().filter(|c| visible.as_ref().is_none_or(|v| v.contains(&as_str(&c["id"])))).collect::<Vec<_>>())
}

async fn create_credential(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let (name, kind) = (as_str(&b["name"]), as_str(&b["type"]));
    if name.is_empty() || kind.is_empty() {
        return Err(err(StatusCode::BAD_REQUEST, "name and type are required"));
    }
    let id = short_id();
    app.db.save_credential(&id, &name, &kind, &app.cipher.encrypt(&b["data"])?)?;
    app.db.set_owner("credentials", &id, &u.id)?;
    ok(json!({"id": id, "name": name, "type": kind}))
}

async fn get_credential(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    access(&app, &u, "credentials", &id)?;
    let (name, kind, cipher) = app.db.credential(&id)?.ok_or_else(|| not_found("Credential"))?;
    let mut data = app.cipher.decrypt(&cipher)?;
    // Tokens never leave the server; the editor only needs to know whether the account is connected.
    if let Some(o) = data.as_object_mut() {
        let connected = o.remove("oauthTokenData").is_some_and(|t| !t.is_null());
        if crate::oauth::is_oauth(&kind) {
            o.insert("oauthConnected".into(), json!(connected));
        }
    }
    for f in secret_fields(&kind) {
        if data.get(&f).is_some_and(|v| !as_str(v).is_empty()) {
            data[f.as_str()] = json!(MASK);
        }
    }
    ok(json!({"id": id, "name": name, "type": kind, "data": data}))
}

async fn update_credential(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    edit_access(&app, &u, "credentials", &id)?;
    let (name, kind, cipher) = app.db.credential(&id)?.ok_or_else(|| not_found("Credential"))?;
    let mut data = app.cipher.decrypt(&cipher)?;
    if !data.is_object() {
        data = json!({});
    }
    for (k, v) in b["data"].as_object().cloned().unwrap_or_default() {
        if v != json!(MASK) && k != "oauthConnected" && k != "oauthTokenData" {
            data[k] = v;
        }
    }
    let name = b["name"].as_str().map(String::from).unwrap_or(name);
    app.db.save_credential(&id, &name, &kind, &app.cipher.encrypt(&data)?)?;
    ok(json!({"id": id, "name": name, "type": kind}))
}

async fn delete_credential(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    if !app.db.can_manage("credentials", &id, &u.id)? {
        return Err(err(StatusCode::FORBIDDEN, "Only the owner of a credential can delete it"));
    }
    app.db.delete_credential(&id)?;
    ok(true)
}

// ---------------------------------------------------------------------------
// OAuth2
// ---------------------------------------------------------------------------

async fn oauth_auth(State(app): State<AppState>, Extension(u): Extension<User>, Query(q): Query<HashMap<String, String>>) -> R {
    let id = q.get("id").ok_or_else(|| err(StatusCode::BAD_REQUEST, "id is required"))?;
    access(&app, &u, "credentials", id)?;
    ok(json!({"url": crate::oauth::auth_url(&app, id)?, "redirectUri": crate::oauth::redirect_uri(&app)}))
}

async fn oauth_callback(State(app): State<AppState>, Query(q): Query<HashMap<String, String>>) -> Html<String> {
    let res = match (q.get("code"), q.get("state"), q.get("error")) {
        (_, _, Some(e)) => Err(anyhow::anyhow!("The provider returned an error: {e} {}", q.get("error_description").cloned().unwrap_or_default())),
        (Some(code), Some(state), _) => crate::oauth::callback(&app, code, state).await,
        _ => Err(anyhow::anyhow!("Missing code or state")),
    };
    let (title, text) = match &res {
        Ok(()) => ("Account connected", "You can close this window and return to Flowwolf.".to_string()),
        Err(e) => ("Connection failed", e.to_string()),
    };
    Html(form_shell(title, &format!(
        r#"<p class="d">{}</p><script>try{{window.opener&&window.opener.postMessage({{type:"oauth",ok:{}}},location.origin)}}catch(e){{}}{}</script>"#,
        esc(&text), res.is_ok(), if res.is_ok() { "setTimeout(()=>window.close(),1200)" } else { "" }
    )))
}

// ---------------------------------------------------------------------------
// resuming waiting executions
// ---------------------------------------------------------------------------

/// Node of a parked execution if it waits for `kind` ("webhook" or "form").
fn waiting_node(app: &AppState, id: &str, kind: &str) -> Option<(i64, Node)> {
    let id: i64 = id.parse().ok()?;
    let (k, node) = app.db.waiting_info(id).ok()??;
    if k != kind {
        return None;
    }
    let ex = app.db.execution(id).ok()??;
    let wf: Workflow = serde_json::from_value(ex["workflowData"].clone()).ok()?;
    Some((id, wf.node(&node)?.clone()))
}

async fn resume_webhook(State(app): State<AppState>, Path(id): Path<String>, Query(q): Query<HashMap<String, String>>, headers: HeaderMap, body: Bytes) -> Response {
    let not_waiting = || (StatusCode::NOT_FOUND, Json(json!({"message": format!("Execution {id} is not waiting for a webhook")}))).into_response();
    let Some((eid, _)) = waiting_node(&app, &id, "webhook") else { return not_waiting() };
    let item = request_item(&headers, &q, Map::new(), &body, format!("{}/webhook-waiting/{id}", app.cfg.base_url), false, false);
    match crate::engine::resume(&app, eid, Some(vec![item])) {
        Ok(true) => Json(json!({"message": "Workflow was resumed"})).into_response(),
        _ => not_waiting(),
    }
}

async fn resume_form_page(State(app): State<AppState>, Path(id): Path<String>) -> Response {
    match waiting_node(&app, &id, "form") {
        Some((_, n)) => Html(render_form(&n, "")).into_response(),
        None => (StatusCode::NOT_FOUND, Html(form_shell("Form expired", "<p class=d>This form is no longer waiting for a response.</p>"))).into_response(),
    }
}

async fn resume_form_submit(State(app): State<AppState>, Path(id): Path<String>, body: Bytes) -> Response {
    let Some((eid, node)) = waiting_node(&app, &id, "form") else { return StatusCode::NOT_FOUND.into_response() };
    let item = form_item(&node, &body, false);
    match crate::engine::resume(&app, eid, Some(vec![item])) {
        Ok(true) => thanks(&node).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

// ---------------------------------------------------------------------------
// variables & API keys
// ---------------------------------------------------------------------------

async fn list_variables(State(app): State<AppState>) -> R {
    ok(app.db.variables()?)
}

fn valid_key(k: &str) -> Result<(), ApiErr> {
    if k.is_empty() || !k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(err(StatusCode::BAD_REQUEST, "Variable keys may only contain letters, numbers and underscores"));
    }
    Ok(())
}

async fn create_variable(State(app): State<AppState>, Json(b): Json<Value>) -> R {
    let (k, v) = (as_str(&b["key"]), as_str(&b["value"]));
    valid_key(&k)?;
    let id = short_id();
    app.db
        .exec("INSERT INTO variables(id, key, value) VALUES(?1, ?2, ?3)", p![&id, &k, &v])
        .map_err(|_| err(StatusCode::BAD_REQUEST, "A variable with this key already exists"))?;
    ok(json!({"id": id, "key": k, "value": v}))
}

async fn update_variable(State(app): State<AppState>, Path(id): Path<String>, Json(b): Json<Value>) -> R {
    let (k, v) = (as_str(&b["key"]), as_str(&b["value"]));
    valid_key(&k)?;
    app.db.exec("UPDATE variables SET key = ?2, value = ?3 WHERE id = ?1", p![&id, &k, &v])?;
    ok(json!({"id": id, "key": k, "value": v}))
}

async fn delete_variable(State(app): State<AppState>, Path(id): Path<String>) -> R {
    app.db.exec("DELETE FROM variables WHERE id = ?1", p![id])?;
    ok(true)
}

async fn list_api_keys(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    let rows = app.db.rows("SELECT id, label, hint, created_at FROM api_keys WHERE user_id = ?1 ORDER BY created_at DESC", p![&u.id])?;
    ok(rows.into_iter().map(|r| json!({"id": r[0].s(), "label": r[1].os(), "apiKey": r[2].s(), "createdAt": r[3].s()})).collect::<Vec<_>>())
}

async fn create_api_key(State(app): State<AppState>, Extension(u): Extension<User>, Json(b): Json<Value>) -> R {
    let key = format!("fw_{}", random_token());
    let id = short_id();
    let hint = format!("{}…{}", &key[..7], &key[key.len() - 4..]);
    app.db.exec("INSERT INTO api_keys(id, user_id, label, key_hash, hint, created_at) VALUES(?1, ?2, ?3, ?4, ?5, ?6)", p![&id, &u.id, as_str(&b["label"]), sha256_hex(&key), hint, now_iso()])?;
    ok(json!({"id": id, "label": as_str(&b["label"]), "apiKey": key}))
}

async fn delete_api_key(State(app): State<AppState>, Extension(u): Extension<User>, Path(id): Path<String>) -> R {
    app.db.exec("DELETE FROM api_keys WHERE id = ?1 AND user_id = ?2", p![id, &u.id])?;
    ok(true)
}

// ---------------------------------------------------------------------------
// live events (Server-Sent Events)
// ---------------------------------------------------------------------------

async fn push(State(app): State<AppState>) -> Sse<impl futures::Stream<Item = Result<Event, Infallible>>> {
    let rx = app.events.subscribe();
    let s = futures::stream::unfold(rx, |mut rx| async move {
        loop {
            match rx.recv().await {
                Ok(v) => return Some((Ok(Event::default().data(v.to_string())), rx)),
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => return None,
            }
        }
    });
    Sse::new(s).keep_alive(KeepAlive::default())
}

// ---------------------------------------------------------------------------
// webhooks
// ---------------------------------------------------------------------------

fn form_decode(s: &str) -> Map<String, Value> {
    let dec = |x: &str| {
        let x = x.replace('+', " ");
        let b = x.as_bytes();
        let mut out = vec![];
        let mut i = 0;
        while i < b.len() {
            if b[i] == b'%' && i + 2 < b.len() {
                if let Ok(v) = u8::from_str_radix(&x[i + 1..i + 3], 16) {
                    out.push(v);
                    i += 3;
                    continue;
                }
            }
            out.push(b[i]);
            i += 1;
        }
        String::from_utf8_lossy(&out).into_owned()
    };
    let mut m = Map::new();
    for pair in s.split('&').filter(|p| !p.is_empty()) {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let (k, v) = (dec(k), dec(v));
        // Repeated keys (checkboxes, multi selects) become arrays.
        match m.get_mut(&k) {
            Some(Value::Array(a)) => a.push(json!(v)),
            Some(prev) => *prev = json!([prev.clone(), v]),
            None => {
                m.insert(k, json!(v));
            }
        }
    }
    m
}

fn multipart_boundary(ct: &str) -> Option<String> {
    ct.split(';').find_map(|p| p.trim().strip_prefix("boundary=")).map(|b| b.trim_matches('"').to_string())
}

struct Part {
    name: String,
    file_name: Option<String>,
    mime: Option<String>,
    data: Vec<u8>,
}

/// Splits a multipart/form-data body into its parts.
fn multipart_parts(body: &[u8], boundary: &str) -> Vec<Part> {
    let find = |hay: &[u8], needle: &[u8], from: usize| hay.get(from..).and_then(|h| h.windows(needle.len()).position(|w| w == needle)).map(|p| p + from);
    let delim = format!("--{boundary}").into_bytes();
    let mut out = vec![];
    let Some(mut pos) = find(body, &delim, 0) else { return out };
    loop {
        let start = pos + delim.len();
        if body.get(start..start + 2) == Some(b"--") {
            break;
        }
        let Some(next) = find(body, &delim, start) else { break };
        let part = &body[start..next];
        let part = part.strip_prefix(b"\r\n").unwrap_or(part);
        let part = part.strip_suffix(b"\r\n").unwrap_or(part);
        if let Some(split) = find(part, b"\r\n\r\n", 0) {
            let head = String::from_utf8_lossy(&part[..split]);
            let attr = |line: &str, key: &str| {
                let k = format!("{key}=\"");
                line.find(&k).map(|i| &line[i + k.len()..]).and_then(|r| r.find('"').map(|e| r[..e].to_string()))
            };
            let (mut name, mut file_name, mut mime) = (String::new(), None, None);
            for line in head.lines() {
                let lower = line.to_lowercase();
                if lower.starts_with("content-disposition") {
                    name = attr(line, "name").unwrap_or_default();
                    file_name = attr(line, "filename");
                } else if let Some(v) = lower.strip_prefix("content-type:") {
                    mime = Some(v.trim().to_string());
                }
            }
            out.push(Part { name, file_name, mime, data: part[split + 4..].to_vec() });
        }
        pos = next;
    }
    out
}

fn request_item(headers: &HeaderMap, query: &HashMap<String, String>, params: Map<String, Value>, body: &Bytes, url: String, raw: bool, test: bool) -> Item {
    let ct = headers.get(header::CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_lowercase();
    let hs: Map<String, Value> = headers.iter().map(|(k, v)| (k.to_string(), json!(v.to_str().unwrap_or("")))).collect();
    let mut binary = None;
    let parsed = if body.is_empty() {
        json!({})
    } else if ct.contains("json") {
        serde_json::from_slice(body).unwrap_or_else(|_| json!(String::from_utf8_lossy(body)))
    } else if ct.contains("x-www-form-urlencoded") {
        Value::Object(form_decode(&String::from_utf8_lossy(body)))
    } else if let Some(boundary) = ct.starts_with("multipart/form-data").then(|| headers.get(header::CONTENT_TYPE).and_then(|v| v.to_str().ok()).and_then(multipart_boundary)).flatten() {
        // Text fields go to the body, files to binary under their field name (as in n8n).
        let (mut fields, mut files) = (Map::new(), Map::new());
        for p in multipart_parts(body, &boundary) {
            match p.file_name {
                Some(f) => {
                    let mime = p.mime.unwrap_or_else(|| crate::nodes::mime_for(&f).to_string());
                    files.insert(p.name, crate::nodes::make_binary(&p.data, &mime, &f));
                }
                None => {
                    fields.insert(p.name, json!(String::from_utf8_lossy(&p.data)));
                }
            }
        }
        if !files.is_empty() {
            binary = Some(files);
        }
        Value::Object(fields)
    } else if ct.starts_with("text/") || ct.contains("xml") || ct.is_empty() {
        json!(String::from_utf8_lossy(body))
    } else {
        let mut m = Map::new();
        m.insert("data".into(), crate::nodes::make_binary(body, ct.split(';').next().unwrap_or("application/octet-stream"), "data"));
        binary = Some(m);
        json!({})
    };
    if raw && binary.is_none() {
        let mut m = Map::new();
        m.insert("data".into(), crate::nodes::make_binary(body, ct.split(';').next().unwrap_or("application/octet-stream"), "data"));
        binary = Some(m);
    }
    Item {
        json: json!({"headers": hs, "params": params, "query": query, "body": parsed, "webhookUrl": url, "executionMode": if test { "test" } else { "production" }}),
        binary,
        paired_item: None,
    }
}

fn check_webhook_auth(app: &AppState, node: &Node, headers: &HeaderMap) -> Result<(), Response> {
    let mode = node.parameters["authentication"].as_str().unwrap_or("none");
    if mode == "none" {
        return Ok(());
    }
    let kind = if mode == "basicAuth" { "httpBasicAuth" } else { "httpHeaderAuth" };
    let deny = |m: &str| (StatusCode::FORBIDDEN, Json(json!({"message": m}))).into_response();
    let creds = node
        .credentials
        .as_ref()
        .and_then(|c| c.get(kind))
        .and_then(|r| r["id"].as_str().map(String::from))
        .and_then(|id| app.db.credential(&id).ok().flatten())
        .and_then(|(_, _, c)| app.cipher.decrypt(&c).ok())
        .ok_or_else(|| deny("No authentication data defined on node"))?;
    let given = |h: &str| headers.get(h).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let valid = if kind == "httpBasicAuth" {
        use base64::Engine as _;
        let expected = base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", as_str(&creds["user"]), as_str(&creds["password"])));
        constant_eq(&given("authorization"), &format!("Basic {expected}"))
    } else {
        constant_eq(&given(&as_str(&creds["name"]).to_lowercase()), &as_str(&creds["value"]))
    };
    if valid {
        Ok(())
    } else {
        let mut r = deny("Authorization data is wrong!");
        if kind == "httpBasicAuth" {
            *r.status_mut() = StatusCode::UNAUTHORIZED;
            r.headers_mut().insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Basic realm=\"Webhook\""));
        }
        Err(r)
    }
}

fn constant_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn reply_response(r: Reply) -> Response {
    let mut res = Response::new(Body::from(r.body));
    *res.status_mut() = StatusCode::from_u16(r.status).unwrap_or(StatusCode::OK);
    for (k, v) in r.headers {
        if let (Ok(k), Ok(v)) = (header::HeaderName::try_from(k), HeaderValue::from_str(&v)) {
            res.headers_mut().append(k, v);
        }
    }
    res
}

/// Runs a webhook triggered workflow and builds the HTTP response according to `responseMode`.
/// MCP Server Trigger (streamable HTTP): JSON-RPC requests to /mcp/<path>.
/// Finds the active MCP Server Trigger for a path and checks its authentication. Paths may
/// carry the `/sse` and `/messages` suffixes of the HTTP+SSE transport.
fn mcp_target(app: &AppState, path: &str, headers: &HeaderMap) -> Result<(Workflow, String, String), Response> {
    let base = [path.strip_suffix("/sse"), path.strip_suffix("/messages")].into_iter().flatten().chain([path])
        .find(|p| triggers::match_hook(app, "MCP", p).is_some());
    let Some(base) = base else {
        return Err((StatusCode::NOT_FOUND, Json(json!({"message": format!("No active MCP server at /mcp/{path}")}))).into_response());
    };
    let (target, _) = triggers::match_hook(app, "MCP", base).ok_or_else(|| StatusCode::NOT_FOUND.into_response())?;
    let Ok(Some(wf)) = app.db.workflow(&target.workflow_id) else { return Err(StatusCode::NOT_FOUND.into_response()) };
    let Some(node) = wf.node(&target.node).cloned() else { return Err(StatusCode::NOT_FOUND.into_response()) };
    let given = |h: &str| headers.get(h).and_then(|v| v.to_str().ok()).unwrap_or_default().to_string();
    let allowed = match node.parameters["authentication"].as_str().unwrap_or("none") {
        "bearerAuth" => triggers::node_credential(app, &node, "httpBearerAuth").is_ok_and(|c| given("authorization") == format!("Bearer {}", as_str(&c["token"]))),
        "headerAuth" => triggers::node_credential(app, &node, "httpHeaderAuth").is_ok_and(|c| given(&as_str(&c["name"]).to_lowercase()) == as_str(&c["value"])),
        _ => true,
    };
    if !allowed {
        return Err((StatusCode::UNAUTHORIZED, Json(json!({"message": "Authorization failed"}))).into_response());
    }
    Ok((wf, target.node, base.to_string()))
}

/// Open HTTP+SSE sessions of MCP Server Triggers: session id -> event stream.
static MCP_SSE: std::sync::LazyLock<std::sync::Mutex<HashMap<String, mpsc::UnboundedSender<Value>>>> = std::sync::LazyLock::new(Default::default);

/// MCP Server Trigger: JSON-RPC requests to /mcp/<path> (streamable HTTP), or to the
/// `/messages?sessionId=` URL announced on an SSE stream (answers then go to that stream).
async fn mcp_endpoint(State(app): State<AppState>, Path(path): Path<String>, Query(q): Query<HashMap<String, String>>, headers: HeaderMap, body: Bytes) -> Response {
    let rpc_err = |code: i64, msg: &str| Json(json!({"jsonrpc": "2.0", "id": null, "error": {"code": code, "message": msg}})).into_response();
    let (wf, node, _) = match mcp_target(&app, &path, &headers) {
        Ok(t) => t,
        Err(r) => return r,
    };
    let stream = match q.get("sessionId") {
        Some(id) => match MCP_SSE.lock().unwrap().get(id) {
            Some(tx) => Some(tx.clone()),
            None => return (StatusCode::NOT_FOUND, "Unknown MCP session").into_response(),
        },
        None => None,
    };
    let Ok(req) = serde_json::from_slice::<Value>(&body) else { return rpc_err(-32700, "Parse error") };
    let id = req["id"].clone();
    let answer = match crate::nodes::cluster::mcp_serve(app.clone(), wf, node, req).await {
        Ok(v) => v,
        Err(e) => Some(json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32603, "message": e.to_string()}})),
    };
    match (stream, answer) {
        (Some(tx), Some(v)) => {
            let _ = tx.send(v);
            StatusCode::ACCEPTED.into_response()
        }
        (None, Some(v)) => Json(v).into_response(),
        (_, None) => StatusCode::ACCEPTED.into_response(),
    }
}

/// HTTP+SSE transport (MCP 2024-11-05): GET opens the event stream and announces where to post.
async fn mcp_get(State(app): State<AppState>, Path(path): Path<String>, headers: HeaderMap) -> Response {
    let base = match mcp_target(&app, &path, &headers) {
        Ok((_, _, base)) => base,
        Err(r) => return r,
    };
    let id = uuid::Uuid::new_v4().simple().to_string();
    let (tx, rx) = mpsc::unbounded_channel::<Value>();
    MCP_SSE.lock().unwrap().insert(id.clone(), tx);
    // Removes the session once the client goes away and the stream is dropped.
    struct Gone(String);
    impl Drop for Gone {
        fn drop(&mut self) {
            MCP_SSE.lock().unwrap().remove(&self.0);
        }
    }
    let first = Event::default().event("endpoint").data(format!("/mcp/{base}/messages?sessionId={id}"));
    let rest = futures::stream::unfold((rx, Gone(id)), |(mut rx, g)| async move {
        let v = rx.recv().await?;
        Some((Ok(Event::default().event("message").data(v.to_string())), (rx, g)))
    });
    Sse::new(futures::stream::once(async { Ok::<_, Infallible>(first) }).chain(rest)).keep_alive(KeepAlive::default()).into_response()
}

/// Answers the checks services make before or while registering a webhook trigger:
/// HEAD/GET pings (Trello, Mailchimp, SurveyMonkey), `hub.challenge` echoes and the Asana
/// `X-Hook-Secret` handshake. Only applies to paths owned by a webhook-registering trigger.
fn service_handshake(app: &AppState, m: &str, path: &str, headers: &HeaderMap, q: &HashMap<String, String>) -> Option<Response> {
    let (target, _) = triggers::match_hook(app, "POST", path)?;
    let wf = app.db.workflow(&target.workflow_id).ok()??;
    let spec = crate::hooktriggers::spec(&wf.node(&target.node)?.kind)?;
    // Events that only test the connection (GitHub "ping") are acknowledged without a run.
    let ignore = &spec["ignore"];
    if let Some(h) = ignore["header"].as_str() {
        let v = headers.get(h).and_then(|v| v.to_str().ok()).unwrap_or_default();
        if ignore["values"].as_array().is_some_and(|a| a.iter().any(|x| x == v)) {
            return Some(StatusCode::OK.into_response());
        }
    }
    if let Some(secret) = headers.get("x-hook-secret") {
        // Asana signs later events with this secret.
        if spec["signature"]["secretFrom"] == "handshake" {
            let key = format!("node:{}", target.node);
            let mut data = wf.static_data.clone();
            if !data.is_object() {
                data = json!({});
            }
            if !data[&key].is_object() {
                data[&key] = json!({});
            }
            data[&key]["secret"] = json!(secret.to_str().unwrap_or_default());
            let _ = app.db.save_static_data(&wf.id, &data);
        }
        let mut r = StatusCode::OK.into_response();
        r.headers_mut().insert("x-hook-secret", secret.clone());
        return Some(r);
    }
    if m == "HEAD" || m == "GET" {
        // Onfleet validates the URL by expecting the `check` value back as plain text.
        if let Some(c) = q.get("check") {
            return Some(c.clone().into_response());
        }
        return Some(match q.get("hub.challenge") {
            Some(c) => Json(json!({"hub.challenge": c})).into_response(),
            None => StatusCode::OK.into_response(),
        });
    }
    None
}

async fn run_webhook(app: AppState, wf: Workflow, node_name: String, item: Item, mode: &str, notify: Option<mpsc::Sender<i64>>) -> Response {
    let node = wf.node(&node_name).cloned().unwrap_or_default();
    let p = &node.parameters;
    let response_mode = p["responseMode"].as_str().unwrap_or("onReceived").to_string();
    let code = as_f64(&p["responseCode"]).or_else(|| as_f64(&p["options"]["responseCode"])).unwrap_or(200.0) as u16;
    let (tx, rx) = oneshot::channel();
    let spec = StartSpec { start_node: Some(node_name.clone()), items: Some(vec![item]), destination: None, ..Default::default() };
    let reply = (response_mode == "responseNode").then_some(tx);
    let (id, done) = match launch(app, Launch { wf, mode: mode.into(), spec, reply, retry_of: None, user: None, exec_id: None }) {
        Ok(x) => x,
        Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"message": e.to_string()}))).into_response(),
    };
    if let Some(n) = notify {
        let _ = n.send(id).await;
    }
    match response_mode.as_str() {
        "responseNode" => match rx.await {
            Ok(r) => reply_response(r),
            Err(_) => {
                let fin = done.await.ok();
                let msg = fin.as_ref().and_then(|f| f.data["resultData"]["error"]["message"].as_str().map(String::from)).unwrap_or("Workflow finished without reaching a 'Respond to Webhook' node".into());
                (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"message": msg}))).into_response()
            }
        },
        "lastNode" => {
            let Ok(fin) = done.await else { return StatusCode::INTERNAL_SERVER_ERROR.into_response() };
            if fin.status != "success" {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"message": "Error in workflow", "error": fin.data["resultData"]["error"]}))).into_response();
            }
            let last = as_str(&fin.data["resultData"]["lastNodeExecuted"]);
            let runs = &fin.data["resultData"]["runData"][&last];
            let items = runs.as_array().and_then(|r| r.last()).map(|t| t["data"]["main"][0].clone()).unwrap_or(json!([]));
            let rd = p["responseData"].as_str().unwrap_or("firstEntryJson");
            match rd {
                "allEntries" => (StatusCode::from_u16(code).unwrap_or(StatusCode::OK), Json(json!(items.as_array().into_iter().flatten().map(|i| i["json"].clone()).collect::<Vec<_>>()))).into_response(),
                "noData" => StatusCode::from_u16(code).unwrap_or(StatusCode::OK).into_response(),
                "firstEntryBinary" => {
                    let prop = p["options"]["binaryPropertyName"].as_str().unwrap_or("data");
                    let bin = &items[0]["binary"][prop];
                    let bytes = crate::nodes::binary_bytes(bin).unwrap_or_default();
                    let mime = bin["mimeType"].as_str().unwrap_or("application/octet-stream").to_string();
                    reply_response(Reply { status: code, headers: vec![("content-type".into(), mime)], body: bytes })
                }
                _ => (StatusCode::from_u16(code).unwrap_or(StatusCode::OK), Json(items[0]["json"].clone())).into_response(),
            }
        }
        _ => {
            let body = match p["options"]["responseData"].as_str() {
                Some("noData") => Value::Null,
                _ => json!({"message": "Workflow was started"}),
            };
            let mut res = if body.is_null() { Response::new(Body::empty()) } else { Json(body).into_response() };
            *res.status_mut() = StatusCode::from_u16(code).unwrap_or(StatusCode::OK);
            res
        }
    }
}

async fn prod_webhook(State(app): State<AppState>, method: Method, Path(path): Path<String>, Query(q): Query<HashMap<String, String>>, headers: HeaderMap, body: Bytes) -> Response {
    if method == Method::OPTIONS {
        return cors_preflight();
    }
    let m = method.as_str().to_uppercase();
    if let Some(r) = service_handshake(&app, &m, &path, &headers, &q) {
        return r;
    }
    let Some((target, params)) = triggers::match_hook(&app, &m, &path) else {
        return (StatusCode::NOT_FOUND, Json(json!({"message": format!("The requested webhook \"{m} {path}\" is not registered.")}))).into_response();
    };
    let Ok(Some(wf)) = app.db.workflow(&target.workflow_id) else { return StatusCode::NOT_FOUND.into_response() };
    let Some(node) = wf.node(&target.node).cloned() else { return StatusCode::NOT_FOUND.into_response() };
    if let Err(r) = check_webhook_auth(&app, &node, &headers) {
        return r;
    }
    if let Err(msg) = crate::hooktriggers::verify(&wf, &node, &headers, &body) {
        tracing::warn!("rejected event for \"{}\": {msg}", node.name);
        return (StatusCode::UNAUTHORIZED, Json(json!({"message": msg}))).into_response();
    }
    let raw = node.parameters["options"]["rawBody"].as_bool().unwrap_or(false);
    let item = request_item(&headers, &q, params, &body, format!("{}/webhook/{path}", app.cfg.base_url), raw, false);
    run_webhook(app, wf, target.node, item, "webhook", None).await
}

async fn test_webhook(State(app): State<AppState>, method: Method, Path(path): Path<String>, Query(q): Query<HashMap<String, String>>, headers: HeaderMap, body: Bytes) -> Response {
    if method == Method::OPTIONS {
        return cors_preflight();
    }
    let key = (method.as_str().to_uppercase(), path.trim_matches('/').to_string());
    let (hook, params) = {
        let mut th = app.test_hooks.lock().unwrap();
        match th.remove(&key) {
            Some(h) => (Some(h), Map::new()),
            None => {
                let found = th.keys().find_map(|k| (k.0 == key.0).then(|| triggers::match_path(&k.1, &key.1).map(|p| (k.clone(), p))).flatten());
                match found {
                    Some((k, p)) => (th.remove(&k), p),
                    None => (None, Map::new()),
                }
            }
        }
    };
    let Some(h) = hook else {
        return (StatusCode::NOT_FOUND, Json(json!({"message": format!("The requested webhook \"{} {}\" is not registered.", key.0, key.1),
            "hint": "Click the 'Listen for test event' button, then make a request to the test URL. Test webhooks only work for one call after clicking."}))).into_response();
    };
    let node = h.wf.node(&h.node).cloned().unwrap_or_default();
    if let Err(r) = check_webhook_auth(&app, &node, &headers) {
        return r;
    }
    let raw = node.parameters["options"]["rawBody"].as_bool().unwrap_or(false);
    let item = request_item(&headers, &q, params, &body, format!("{}/webhook-test/{path}", app.cfg.base_url), raw, true);
    run_webhook(app, h.wf, h.node, item, "manual", Some(h.notify)).await
}

fn cors_preflight() -> Response {
    let mut r = StatusCode::NO_CONTENT.into_response();
    let h = r.headers_mut();
    h.insert("access-control-allow-origin", HeaderValue::from_static("*"));
    h.insert("access-control-allow-methods", HeaderValue::from_static("GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"));
    h.insert("access-control-allow-headers", HeaderValue::from_static("*"));
    r
}

// ---------------------------------------------------------------------------
// forms
// ---------------------------------------------------------------------------

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn render_form(node: &Node, action: &str) -> String {
    let p = &node.parameters;
    let mut fields = String::new();
    for (i, f) in p["formFields"]["values"].as_array().into_iter().flatten().enumerate() {
        let label = esc(&as_str(&f["fieldLabel"]));
        let req = if f["requiredField"] == true { " required" } else { "" };
        let ph = esc(&as_str(&f["placeholder"]));
        let name = format!("field-{i}");
        let input = match f["fieldType"].as_str().unwrap_or("text") {
            "textarea" => format!(r#"<textarea name="{name}" placeholder="{ph}"{req}></textarea>"#),
            "dropdown" => {
                let multi = f["multiselect"] == true;
                let opts: String = f["fieldOptions"]["values"].as_array().into_iter().flatten().map(|o| format!("<option>{}</option>", esc(&as_str(&o["option"])))).collect();
                format!(r#"<select name="{name}"{}{req}>{}{opts}</select>"#, if multi { " multiple" } else { "" }, if multi { "" } else { "<option value=\"\"></option>" })
            }
            t @ ("email" | "number" | "password" | "date") => format!(r#"<input type="{t}" name="{name}" placeholder="{ph}"{req}>"#),
            _ => format!(r#"<input type="text" name="{name}" placeholder="{ph}"{req}>"#),
        };
        fields.push_str(&format!(r#"<label><span>{label}{}</span>{input}</label>"#, if req.is_empty() { "" } else { " <i>*</i>" }));
    }
    let button = esc(p["options"]["buttonLabel"].as_str().unwrap_or("Submit"));
    form_shell(&as_str(&p["formTitle"]), &format!(
        r#"<p class="d">{}</p><form method="post" action="{action}">{fields}<button type="submit">{button}</button></form>"#,
        esc(&as_str(&p["formDescription"]))
    ))
}

fn form_shell(title: &str, inner: &str) -> String {
    format!(
        r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{t}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font:16px/1.5 Inter,system-ui,sans-serif;background:#f6f7f9;color:#0b0b0f;display:grid;place-items:center;min-height:100vh;padding:24px}}
.c{{background:#fff;border:1px solid #e7e8ec;border-radius:20px;padding:36px;width:100%;max-width:520px;box-shadow:0 10px 40px -20px rgba(0,0,0,.15)}}h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.02em}}
.d{{color:#5b5f6b;margin:0 0 24px}}label{{display:block;margin-bottom:16px}}label span{{display:block;font-weight:600;font-size:14px;margin-bottom:6px}}i{{color:#e5484d;font-style:normal}}
input,textarea,select{{width:100%;padding:12px 14px;border:1px solid #dcdee3;border-radius:12px;font:inherit;background:#fff}}textarea{{min-height:110px}}
input:focus,textarea:focus,select:focus{{outline:2px solid #0b0b0f;outline-offset:1px}}button{{width:100%;padding:13px;border:0;border-radius:999px;background:#0b0b0f;color:#fff;font:600 15px Inter,system-ui;cursor:pointer}}
button:hover{{background:#26262e}}.f{{text-align:center;color:#9a9ea9;font-size:12px;margin-top:20px}}</style></head>
<body><div class="c"><h1>{t}</h1>{inner}<div class="f">Powered by Flowwolf</div></div></body></html>"#,
        t = esc(title)
    )
}

fn form_item(node: &Node, body: &Bytes, test: bool) -> Item {
    let data = form_decode(&String::from_utf8_lossy(body));
    let mut o = Map::new();
    for (i, f) in node.parameters["formFields"]["values"].as_array().into_iter().flatten().enumerate() {
        let v = data.get(&format!("field-{i}")).cloned().unwrap_or(json!(""));
        let v = if f["fieldType"] == "number" { as_f64(&v).map(|n| json!(n)).unwrap_or(v) } else { v };
        o.insert(as_str(&f["fieldLabel"]), v);
    }
    o.insert("submittedAt".into(), json!(now_iso()));
    o.insert("formMode".into(), json!(if test { "test" } else { "production" }));
    Item::new(Value::Object(o))
}

fn thanks(node: &Node) -> Html<String> {
    let text = node.parameters["options"]["respondWithOptions"]["values"]["formSubmittedText"].as_str().unwrap_or("Your response has been recorded");
    Html(form_shell(node.parameters["formTitle"].as_str().unwrap_or("Thank you"), &format!(r#"<p class="d">{}</p>"#, esc(text))))
}

async fn form_page(State(app): State<AppState>, Path(path): Path<String>) -> Response {
    let Some((t, _)) = triggers::match_hook(&app, "FORM", &path) else { return (StatusCode::NOT_FOUND, Html(form_shell("Form not found", "<p class=d>This form is not active.</p>"))).into_response() };
    let Some(node) = app.db.workflow(&t.workflow_id).ok().flatten().and_then(|w| w.node(&t.node).cloned()) else { return StatusCode::NOT_FOUND.into_response() };
    Html(render_form(&node, "")).into_response()
}

async fn form_submit(State(app): State<AppState>, Path(path): Path<String>, body: Bytes) -> Response {
    let Some((t, _)) = triggers::match_hook(&app, "FORM", &path) else { return StatusCode::NOT_FOUND.into_response() };
    let Ok(Some(wf)) = app.db.workflow(&t.workflow_id) else { return StatusCode::NOT_FOUND.into_response() };
    let node = wf.node(&t.node).cloned().unwrap_or_default();
    let item = form_item(&node, &body, false);
    let _ = launch(app, Launch { wf, mode: "webhook".into(), spec: StartSpec { start_node: Some(t.node), items: Some(vec![item]), destination: None, ..Default::default() }, reply: None, retry_of: None, user: None, exec_id: None });
    thanks(&node).into_response()
}

async fn form_page_test(State(app): State<AppState>, Path(path): Path<String>) -> Response {
    let key = ("FORM".to_string(), path.trim_matches('/').to_string());
    let node = app.test_hooks.lock().unwrap().get(&key).and_then(|h| h.wf.node(&h.node).cloned());
    match node {
        Some(n) => Html(render_form(&n, "")).into_response(),
        None => (StatusCode::NOT_FOUND, Html(form_shell("Form not listening", "<p class=d>Click 'Test workflow' in the editor first.</p>"))).into_response(),
    }
}

async fn form_submit_test(State(app): State<AppState>, Path(path): Path<String>, body: Bytes) -> Response {
    let key = ("FORM".to_string(), path.trim_matches('/').to_string());
    let Some(h) = app.test_hooks.lock().unwrap().remove(&key) else { return StatusCode::NOT_FOUND.into_response() };
    let node = h.wf.node(&h.node).cloned().unwrap_or_default();
    let item = form_item(&node, &body, true);
    if let Ok((id, _)) = launch(app, Launch { wf: h.wf, mode: "manual".into(), spec: StartSpec { start_node: Some(h.node), items: Some(vec![item]), destination: None, ..Default::default() }, reply: None, retry_of: None, user: None, exec_id: None }) {
        let _ = h.notify.send(id).await;
    }
    thanks(&node).into_response()
}

// ---------------------------------------------------------------------------
// hosted chat (Chat Trigger)
// ---------------------------------------------------------------------------

fn chat_html(node: &Node) -> String {
    let p = &node.parameters;
    let title = esc(p["options"]["title"].as_str().unwrap_or("Hi there! 👋"));
    let sub = esc(p["options"]["subtitle"].as_str().unwrap_or("Start a chat. We're here to help you 24/7."));
    let first = esc(p["initialMessages"].as_str().unwrap_or("Hi there! How can I help you today?"));
    format!(r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font:15px/1.5 Inter,system-ui,sans-serif;background:#f6f7f9;color:#0b0b0f;display:flex;justify-content:center;min-height:100vh}}
.c{{width:100%;max-width:720px;display:flex;flex-direction:column;height:100vh;background:#fff;border-left:1px solid #ececf0;border-right:1px solid #ececf0}}
header{{padding:20px 22px;border-bottom:1px solid #ececf0}}h1{{margin:0;font-size:20px;letter-spacing:-.02em}}header p{{margin:2px 0 0;color:#5d616d;font-size:14px}}
#log{{flex:1;overflow:auto;padding:20px 22px;display:flex;flex-direction:column;gap:10px}}.m{{max-width:80%;padding:10px 14px;border-radius:16px;white-space:pre-wrap;word-break:break-word}}
.b{{background:#f1f2f5;align-self:flex-start;border-bottom-left-radius:4px}}.u{{background:#0b0b0f;color:#fff;align-self:flex-end;border-bottom-right-radius:4px}}
form{{display:flex;gap:8px;padding:14px 16px;border-top:1px solid #ececf0}}input{{flex:1;padding:12px 14px;border:1px solid #dcdee3;border-radius:999px;font:inherit;outline:none}}
input:focus{{border-color:#4f6bff}}button{{border:0;border-radius:999px;background:#0b0b0f;color:#fff;padding:0 20px;font:600 14px Inter,system-ui;cursor:pointer}}button:disabled{{opacity:.5}}</style></head>
<body><div class="c"><header><h1>{title}</h1><p>{sub}</p></header><div id="log"><div class="m b">{first}</div></div>
<form id="f"><input id="i" placeholder="Type your question…" autocomplete="off" autofocus><button>Send</button></form></div>
<script>const sid=(crypto.randomUUID?crypto.randomUUID():String(Math.random()).slice(2));const log=document.getElementById('log');
const add=(t,c)=>{{const d=document.createElement('div');d.className='m '+c;d.textContent=t;log.append(d);log.scrollTop=log.scrollHeight;return d}};
document.getElementById('f').onsubmit=async e=>{{e.preventDefault();const i=document.getElementById('i');const t=i.value.trim();if(!t)return;i.value='';add(t,'u');
const w=add('…','b');const b=e.target.querySelector('button');b.disabled=true;try{{const r=await fetch(location.pathname,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:'sendMessage',sessionId:sid,chatInput:t}})}});
const j=await r.json();w.textContent=j.output??j.text??j.message??JSON.stringify(j)}}catch(x){{w.textContent='Error: '+x.message}}b.disabled=false;i.focus()}};</script></body></html>"#)
}

async fn chat_reply(app: AppState, wf: Workflow, node: String, body: Bytes, mode: &str, notify: Option<mpsc::Sender<i64>>) -> Response {
    let mut j: Value = serde_json::from_slice(&body).unwrap_or(json!({}));
    if !j.is_object() {
        j = json!({});
    }
    j["action"] = json!(j["action"].as_str().unwrap_or("sendMessage"));
    let spec = StartSpec { start_node: Some(node), items: Some(vec![Item::new(j)]), destination: None, ..Default::default() };
    let Ok((id, done)) = launch(app, Launch { wf, mode: mode.into(), spec, reply: None, retry_of: None, user: None, exec_id: None }) else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    if let Some(n) = notify {
        let _ = n.send(id).await;
    }
    let Ok(fin) = done.await else { return StatusCode::INTERNAL_SERVER_ERROR.into_response() };
    if fin.status != "success" {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"message": fin.data["resultData"]["error"]["message"]}))).into_response();
    }
    let last = as_str(&fin.data["resultData"]["lastNodeExecuted"]);
    let first = fin.data["resultData"]["runData"][&last].as_array().and_then(|r| r.last()).map(|t| t["data"]["main"][0][0]["json"].clone()).unwrap_or(json!({}));
    Json(first).into_response()
}

async fn chat_page(State(app): State<AppState>, Path(path): Path<String>) -> Response {
    let Some((t, _)) = triggers::match_hook(&app, "CHAT", &path) else { return (StatusCode::NOT_FOUND, Html(form_shell("Chat not found", "<p class=d>This chat is not active.</p>"))).into_response() };
    let Some(node) = app.db.workflow(&t.workflow_id).ok().flatten().and_then(|w| w.node(&t.node).cloned()) else { return StatusCode::NOT_FOUND.into_response() };
    Html(chat_html(&node)).into_response()
}

async fn chat_message(State(app): State<AppState>, Path(path): Path<String>, body: Bytes) -> Response {
    let Some((t, _)) = triggers::match_hook(&app, "CHAT", &path) else { return StatusCode::NOT_FOUND.into_response() };
    let Ok(Some(wf)) = app.db.workflow(&t.workflow_id) else { return StatusCode::NOT_FOUND.into_response() };
    chat_reply(app, wf, t.node, body, "webhook", None).await
}

async fn chat_page_test(State(app): State<AppState>, Path(path): Path<String>) -> Response {
    let key = ("CHAT".to_string(), path.trim_matches('/').to_string());
    let node = app.test_hooks.lock().unwrap().get(&key).and_then(|h| h.wf.node(&h.node).cloned());
    match node {
        Some(n) => Html(chat_html(&n)).into_response(),
        None => (StatusCode::NOT_FOUND, Html(form_shell("Chat not listening", "<p class=d>Click 'Test workflow' in the editor first.</p>"))).into_response(),
    }
}

async fn chat_message_test(State(app): State<AppState>, Path(path): Path<String>, body: Bytes) -> Response {
    let key = ("CHAT".to_string(), path.trim_matches('/').to_string());
    // The test chat stays registered for follow-up messages, so it is cloned instead of removed.
    let hook = app.test_hooks.lock().unwrap().get(&key).map(|h| (h.wf.clone(), h.node.clone(), h.notify.clone()));
    let Some((wf, node, notify)) = hook else { return StatusCode::NOT_FOUND.into_response() };
    chat_reply(app, wf, node, body, "manual", Some(notify)).await
}

// ---------------------------------------------------------------------------
// static files (landing page + editor), embedded into the binary
// ---------------------------------------------------------------------------

async fn static_file(uri: axum::http::Uri, headers: HeaderMap) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = match path {
        "" => "index.html".to_string(),
        "app" | "app/" => "app/index.html".to_string(),
        p if p.ends_with('/') => format!("{p}index.html"),
        p => p.to_string(),
    };
    let Some(f) = Assets::get(&path).or_else(|| (!path.contains('.')).then(|| Assets::get(&format!("{path}.html"))).flatten()) else {
        return (StatusCode::NOT_FOUND, Html("<h1>404</h1>")).into_response();
    };
    let mime = if path.ends_with(".html") { "text/html; charset=utf-8" } else { crate::nodes::mime_for(&path) };
    // Revalidate every time; the ETag makes that a cheap 304.
    let etag = format!("\"{}\"", hex::encode(&f.metadata.sha256_hash()[..12]));
    if headers.get(header::IF_NONE_MATCH).is_some_and(|v| v.as_bytes() == etag.as_bytes()) {
        return (StatusCode::NOT_MODIFIED, [(header::ETAG, etag)]).into_response();
    }
    ([(header::CONTENT_TYPE, mime.to_string()), (header::CACHE_CONTROL, "no-cache".into()), (header::ETAG, etag)], f.data.into_owned()).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multipart_bodies() {
        let body = b"--XB\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n42\r\n--XB\r\nContent-Disposition: form-data; name=\"document\"; filename=\"r.pdf\"\r\nContent-Type: application/pdf\r\n\r\n%PDF\r\n1\r\n--XB--\r\n";
        assert_eq!(multipart_boundary("multipart/form-data; boundary=\"XB\"").as_deref(), Some("XB"));
        let parts = multipart_parts(body, "XB");
        assert_eq!(parts.len(), 2);
        assert_eq!((parts[0].name.as_str(), parts[0].data.as_slice()), ("chat_id", b"42".as_slice()));
        assert_eq!((parts[1].file_name.as_deref(), parts[1].mime.as_deref(), parts[1].data.as_slice()), (Some("r.pdf"), Some("application/pdf"), b"%PDF\r\n1".as_slice()));
    }

    #[test]
    fn decoding() {
        let m = form_decode("a=1&b=hello+world%21&c=x&c=y");
        assert_eq!(m["b"], json!("hello world!"));
        assert_eq!(m["c"], json!(["x", "y"]));
        assert!(constant_eq("abc", "abc") && !constant_eq("abc", "abd"));
    }
}
