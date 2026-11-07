//! Persistence: SQLite (default, one file) or PostgreSQL (DB_TYPE=postgresdb, for workers on several hosts).
//! All SQL is written once in the SQLite dialect with numbered parameters; `pg_sql` translates the few
//! differences. Values cross the boundary as `V`, so callers never see a driver type.
// ponytail: one connection per process (mutex / one Postgres thread); add a pool if contention ever shows up.

use crate::model::{Workflow, now_iso};
use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use std::sync::Mutex;

/// A SQL value.
#[derive(Clone, Debug, PartialEq)]
pub enum V {
    Null,
    I(i64),
    F(f64),
    T(String),
    B(Vec<u8>),
}

impl V {
    pub fn s(&self) -> String {
        match self {
            V::T(s) => s.clone(),
            V::I(i) => i.to_string(),
            V::F(f) => f.to_string(),
            _ => String::new(),
        }
    }
    pub fn os(&self) -> Option<String> {
        (!matches!(self, V::Null)).then(|| self.s())
    }
    pub fn i(&self) -> i64 {
        match self {
            V::I(i) => *i,
            V::F(f) => *f as i64,
            V::T(s) => s.parse().unwrap_or(0),
            _ => 0,
        }
    }
    pub fn oi(&self) -> Option<i64> {
        (!matches!(self, V::Null)).then(|| self.i())
    }
    pub fn bytes(&self) -> Vec<u8> {
        match self {
            V::B(b) => b.clone(),
            V::T(s) => s.as_bytes().to_vec(),
            _ => vec![],
        }
    }
    /// JSON stored as text.
    pub fn json(&self) -> Value {
        self.os().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null)
    }
}

macro_rules! from_into {
    ($($t:ty => $e:expr),* $(,)?) => { $(impl From<$t> for V { fn from(x: $t) -> V { #[allow(clippy::redundant_closure_call)] ($e)(x) } })* };
}
from_into! {
    &str => |x: &str| V::T(x.to_string()),
    String => V::T,
    &String => |x: &String| V::T(x.clone()),
    i64 => V::I,
    i32 => |x: i32| V::I(x as i64),
    u16 => |x: u16| V::I(x as i64),
    usize => |x: usize| V::I(x as i64),
    bool => |x: bool| V::I(x as i64),
    f64 => V::F,
    Vec<u8> => V::B,
    &[u8] => |x: &[u8]| V::B(x.to_vec()),
}
impl<T: Into<V>> From<Option<T>> for V {
    fn from(x: Option<T>) -> V {
        x.map(Into::into).unwrap_or(V::Null)
    }
}

/// Builds a parameter list: `p![id, name, Some(3)]`.
#[macro_export]
macro_rules! p {
    ($($x:expr),* $(,)?) => { &[$($crate::db::V::from($x)),*][..] };
}

pub type Row = Vec<V>;

enum Conn {
    Lite(Mutex<rusqlite::Connection>),
    Pg(pg::Pg),
}

pub struct Db(Conn);

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, first_name TEXT, last_name TEXT,
  password TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, label TEXT,
  key_hash TEXT UNIQUE NOT NULL, hint TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, nodes TEXT NOT NULL,
  connections TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}', static_data TEXT NOT NULL DEFAULT '{}',
  pin_data TEXT NOT NULL DEFAULT '{}', tags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  folder TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS credentials (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, workflow_name TEXT, mode TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, stopped_at TEXT, retry_of INTEGER,
  data TEXT, workflow_data TEXT);
CREATE INDEX IF NOT EXISTS executions_wf ON executions(workflow_id, id);
CREATE TABLE IF NOT EXISTS variables (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS waiting (exec_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, resume_at INTEGER, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS replies (exec_id INTEGER PRIMARY KEY, status INTEGER NOT NULL, headers TEXT NOT NULL, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS shares (resource TEXT NOT NULL, resource_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY (resource, resource_id, user_id));
"#;

/// SQLite dialect -> PostgreSQL: $n parameters, column types, row locking for the job queue.
fn pg_sql(sql: &str) -> String {
    let mut out = String::with_capacity(sql.len());
    let mut chars = sql.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '?' && chars.peek().is_some_and(|d| d.is_ascii_digit()) {
            out.push('$');
        } else {
            out.push(c);
        }
    }
    out.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        .replace("INTEGER", "BIGINT")
        .replace("BLOB", "BYTEA")
        .replace(" REAL", " DOUBLE PRECISION")
        .replace("/*SKIP_LOCKED*/", "FOR UPDATE SKIP LOCKED")
}

const WF_COLS: &str = "id, name, active, nodes, connections, settings, static_data, pin_data, tags, created_at, updated_at, folder";

fn to_workflow(r: &Row) -> Workflow {
    Workflow {
        id: r[0].s(),
        name: r[1].s(),
        active: r[2].i() != 0,
        nodes: serde_json::from_str(&r[3].s()).unwrap_or_default(),
        connections: r[4].json(),
        settings: r[5].json(),
        static_data: r[6].json(),
        pin_data: r[7].json(),
        tags: serde_json::from_str(&r[8].s()).unwrap_or_default(),
        created_at: r[9].s(),
        updated_at: r[10].s(),
        folder: r[11].s(),
    }
}

impl Db {
    /// SQLite file (or ":memory:").
    pub fn open(path: &str) -> Result<Db> {
        let conn = rusqlite::Connection::open(path)?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;")?;
        Db::init(Db(Conn::Lite(Mutex::new(conn))))
    }

    /// PostgreSQL, configured like n8n (DB_POSTGRESDB_* variables are read by the caller into `cfg`).
    pub fn postgres(cfg: Value) -> Result<Db> {
        Db::init(Db(Conn::Pg(pg::Pg::start(cfg)?)))
    }

    /// Picks the backend from the environment (DB_TYPE=postgresdb) or falls back to SQLite in `data_dir`.
    pub fn from_env(data_dir: &str) -> Result<Db> {
        let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        if env("DB_TYPE").as_deref() == Some("postgresdb") {
            let cfg = json!({
                "host": env("DB_POSTGRESDB_HOST").unwrap_or_else(|| "localhost".into()),
                "port": env("DB_POSTGRESDB_PORT").and_then(|p| p.parse::<f64>().ok()).unwrap_or(5432.0),
                "database": env("DB_POSTGRESDB_DATABASE").unwrap_or_else(|| "n8n".into()),
                "user": env("DB_POSTGRESDB_USER").unwrap_or_else(|| "postgres".into()),
                "password": env("DB_POSTGRESDB_PASSWORD").unwrap_or_default(),
                "schema": env("DB_POSTGRESDB_SCHEMA").unwrap_or_else(|| "public".into()),
                "ssl": if env("DB_POSTGRESDB_SSL_ENABLED").as_deref() == Some("true") { "require" } else { "disable" },
                "allowUnauthorizedCerts": env("DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED").as_deref() == Some("false"),
            });
            tracing::info!("database: PostgreSQL {}:{}/{}", cfg["host"].as_str().unwrap_or(""), cfg["port"].as_f64().unwrap_or(5432.0) as u16, cfg["database"].as_str().unwrap_or(""));
            return Db::postgres(cfg);
        }
        Db::open(&format!("{data_dir}/flowwolf.sqlite"))
    }

    fn init(db: Db) -> Result<Db> {
        db.batch(SCHEMA)?;
        db.migrate()?;
        Ok(db)
    }

    pub fn is_postgres(&self) -> bool {
        matches!(self.0, Conn::Pg(_))
    }

    /// Runs one statement. Returns (affected rows, returned rows).
    fn run(&self, sql: &str, params: &[V]) -> Result<(usize, Vec<Row>)> {
        match &self.0 {
            Conn::Lite(m) => {
                let c = m.lock().map_err(|_| anyhow!("db lock poisoned"))?;
                lite::run(&c, sql, params)
            }
            Conn::Pg(p) => p.run(pg_sql(sql), params.to_vec()),
        }
    }

    /// Several statements separated by ';' (schema setup, no parameters).
    pub fn batch(&self, sql: &str) -> Result<()> {
        match &self.0 {
            Conn::Lite(m) => Ok(m.lock().map_err(|_| anyhow!("db lock poisoned"))?.execute_batch(sql)?),
            Conn::Pg(p) => {
                for stmt in sql.split(';').map(str::trim).filter(|s| !s.is_empty()) {
                    p.run(pg_sql(stmt), vec![])?;
                }
                Ok(())
            }
        }
    }

    pub fn exec(&self, sql: &str, params: &[V]) -> Result<usize> {
        Ok(self.run(sql, params)?.0)
    }

    pub fn rows(&self, sql: &str, params: &[V]) -> Result<Vec<Row>> {
        Ok(self.run(sql, params)?.1)
    }

    pub fn row(&self, sql: &str, params: &[V]) -> Result<Option<Row>> {
        Ok(self.rows(sql, params)?.into_iter().next())
    }

    fn has_column(&self, table: &str, col: &str) -> Result<bool> {
        let sql = if self.is_postgres() {
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?1 AND column_name = ?2"
        } else {
            "SELECT 1 FROM pragma_table_info(?1) WHERE name = ?2"
        };
        Ok(self.row(sql, p![table, col])?.is_some())
    }

    /// Adds columns introduced after the first release; unowned rows go to the instance owner.
    pub fn migrate(&self) -> Result<()> {
        for t in ["workflows", "credentials"] {
            if !self.has_column(t, "owner_id")? {
                self.exec(&format!("ALTER TABLE {t} ADD COLUMN owner_id TEXT"), &[])?;
            }
            self.exec(&format!("UPDATE {t} SET owner_id = (SELECT id FROM users WHERE role = 'owner' LIMIT 1) WHERE owner_id IS NULL"), &[])?;
        }
        if !self.has_column("workflows", "folder")? {
            self.exec("ALTER TABLE workflows ADD COLUMN folder TEXT NOT NULL DEFAULT ''", &[])?;
        }
        for col in ["queue_payload", "worker"] {
            if !self.has_column("executions", col)? {
                self.exec(&format!("ALTER TABLE executions ADD COLUMN {col} TEXT"), &[])?;
            }
        }
        for (col, kind) in crate::users::USER_MIGRATIONS {
            if !self.has_column("users", col)? {
                self.exec(&format!("ALTER TABLE users ADD COLUMN {col} {kind}"), &[])?;
            }
        }
        if !self.has_column("shares", "role")? {
            self.exec("ALTER TABLE shares ADD COLUMN role TEXT NOT NULL DEFAULT 'editor'", &[])?;
        }
        Ok(())
    }

    // `table` is always one of the constants "workflows" / "credentials".
    pub fn set_owner(&self, table: &str, id: &str, user: &str) -> Result<()> {
        self.exec(&format!("UPDATE {table} SET owner_id = ?2 WHERE id = ?1"), p![id, user])?;
        Ok(())
    }

    pub fn owner_of(&self, table: &str, id: &str) -> Result<Option<String>> {
        Ok(self.row(&format!("SELECT owner_id FROM {table} WHERE id = ?1"), p![id])?.and_then(|r| r[0].os()))
    }

    /// (user id, role) pairs; role is "viewer" or "editor".
    pub fn shared_with(&self, table: &str, id: &str) -> Result<Vec<(String, String)>> {
        Ok(self.rows("SELECT user_id, role FROM shares WHERE resource = ?1 AND resource_id = ?2", p![table, id])?.into_iter().map(|r| (r[0].s(), r[1].s())).collect())
    }

    pub fn set_shares(&self, table: &str, id: &str, users: &[(String, String)]) -> Result<()> {
        self.exec("DELETE FROM shares WHERE resource = ?1 AND resource_id = ?2", p![table, id])?;
        for (u, role) in users {
            let role = if role == "viewer" { "viewer" } else { "editor" };
            self.exec(
                "INSERT INTO shares(resource, resource_id, user_id, role) SELECT ?1, ?2, id, ?4 FROM users WHERE id = ?3 ON CONFLICT DO NOTHING",
                p![table, id, u, role],
            )?;
        }
        Ok(())
    }

    /// Owner, admin or a share with the editor role.
    pub fn can_edit(&self, table: &str, id: &str, user: &str) -> Result<bool> {
        if self.can_manage(table, id, user)? {
            return Ok(true);
        }
        Ok(self.row("SELECT role FROM shares WHERE resource = ?1 AND resource_id = ?2 AND user_id = ?3", p![table, id, user])?.is_some_and(|r| r[0].s() == "editor"))
    }

    pub fn is_admin(&self, user: &str) -> Result<bool> {
        Ok(self.row("SELECT role FROM users WHERE id = ?1", p![user])?.is_some_and(|r| matches!(r[0].s().as_str(), "owner" | "admin")))
    }

    /// Ids the user may see; None means everything (owner and admins).
    pub fn visible_ids(&self, table: &str, user: &str) -> Result<Option<std::collections::HashSet<String>>> {
        if self.is_admin(user)? {
            return Ok(None);
        }
        let sql = format!("SELECT id FROM {table} WHERE owner_id = ?1 UNION SELECT resource_id FROM shares WHERE resource = ?2 AND user_id = ?1");
        Ok(Some(self.rows(&sql, p![user, table])?.into_iter().map(|r| r[0].s()).collect()))
    }

    pub fn can_access(&self, table: &str, id: &str, user: &str) -> Result<bool> {
        Ok(self.visible_ids(table, user)?.is_none_or(|ids| ids.contains(id)))
    }

    /// Owner of the resource or an admin.
    pub fn can_manage(&self, table: &str, id: &str, user: &str) -> Result<bool> {
        Ok(self.is_admin(user)? || self.owner_of(table, id)?.as_deref() == Some(user))
    }

    // ---- settings ----
    pub fn setting(&self, key: &str) -> Result<Option<String>> {
        Ok(self.row("SELECT value FROM settings WHERE key = ?1", p![key])?.map(|r| r[0].s()))
    }

    pub fn set_setting(&self, key: &str, value: &str) -> Result<()> {
        self.exec("INSERT INTO settings(key, value) VALUES(?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2", p![key, value])?;
        Ok(())
    }

    // ---- workflows ----
    pub fn workflows(&self) -> Result<Vec<Workflow>> {
        Ok(self.rows(&format!("SELECT {WF_COLS} FROM workflows ORDER BY updated_at DESC"), &[])?.iter().map(to_workflow).collect())
    }

    pub fn workflow(&self, id: &str) -> Result<Option<Workflow>> {
        Ok(self.row(&format!("SELECT {WF_COLS} FROM workflows WHERE id = ?1"), p![id])?.as_ref().map(to_workflow))
    }

    pub fn save_workflow(&self, w: &Workflow) -> Result<()> {
        let s = |v: &Value| if v.is_null() { "{}".to_string() } else { v.to_string() };
        self.exec(
            "INSERT INTO workflows(id, name, active, nodes, connections, settings, static_data, pin_data, tags, created_at, updated_at, folder)
             VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
             ON CONFLICT(id) DO UPDATE SET name = ?2, active = ?3, nodes = ?4, connections = ?5, settings = ?6,
               static_data = ?7, pin_data = ?8, tags = ?9, updated_at = ?11, folder = ?12",
            p![
                &w.id,
                &w.name,
                w.active,
                serde_json::to_string(&w.nodes)?,
                s(&w.connections),
                s(&w.settings),
                s(&w.static_data),
                s(&w.pin_data),
                serde_json::to_string(&w.tags)?,
                &w.created_at,
                &w.updated_at,
                w.folder.trim_matches('/'),
            ],
        )?;
        Ok(())
    }

    pub fn save_static_data(&self, id: &str, data: &Value) -> Result<()> {
        self.exec("UPDATE workflows SET static_data = ?2 WHERE id = ?1", p![id, data.to_string()])?;
        Ok(())
    }

    pub fn delete_workflow(&self, id: &str) -> Result<()> {
        self.exec("DELETE FROM workflows WHERE id = ?1", p![id])?;
        Ok(())
    }

    // ---- credentials (data column holds ciphertext) ----
    pub fn credentials(&self) -> Result<Vec<Value>> {
        Ok(self
            .rows("SELECT id, name, type, created_at, updated_at FROM credentials ORDER BY name", &[])?
            .into_iter()
            .map(|r| json!({"id": r[0].s(), "name": r[1].s(), "type": r[2].s(), "createdAt": r[3].s(), "updatedAt": r[4].s()}))
            .collect())
    }

    /// Returns (name, type, ciphertext).
    pub fn credential(&self, id: &str) -> Result<Option<(String, String, String)>> {
        Ok(self.row("SELECT name, type, data FROM credentials WHERE id = ?1", p![id])?.map(|r| (r[0].s(), r[1].s(), r[2].s())))
    }

    pub fn credential_by_name(&self, name: &str, kind: &str) -> Result<Option<String>> {
        Ok(self.row("SELECT id FROM credentials WHERE name = ?1 AND type = ?2", p![name, kind])?.map(|r| r[0].s()))
    }

    pub fn save_credential(&self, id: &str, name: &str, kind: &str, cipher: &str) -> Result<()> {
        self.exec(
            "INSERT INTO credentials(id, name, type, data, created_at, updated_at) VALUES(?1, ?2, ?3, ?4, ?5, ?5)
             ON CONFLICT(id) DO UPDATE SET name = ?2, type = ?3, data = ?4, updated_at = ?5",
            p![id, name, kind, cipher, now_iso()],
        )?;
        Ok(())
    }

    pub fn delete_credential(&self, id: &str) -> Result<()> {
        self.exec("DELETE FROM credentials WHERE id = ?1", p![id])?;
        Ok(())
    }

    // ---- executions ----
    pub fn create_execution(&self, wf: &Workflow, mode: &str, retry_of: Option<i64>) -> Result<i64> {
        let r = self.row(
            "INSERT INTO executions(workflow_id, workflow_name, mode, status, started_at, retry_of, workflow_data)
             VALUES(?1, ?2, ?3, 'running', ?4, ?5, ?6) RETURNING id",
            p![&wf.id, &wf.name, mode, now_iso(), retry_of, serde_json::to_string(wf)?],
        )?;
        Ok(r.ok_or_else(|| anyhow!("no execution id"))?[0].i())
    }

    // ---- queue mode ----
    pub fn enqueue(&self, id: i64, payload: &Value) -> Result<()> {
        self.exec("UPDATE executions SET status = 'queued', queue_payload = ?2 WHERE id = ?1", p![id, payload.to_string()])?;
        Ok(())
    }

    /// Atomically takes the oldest queued execution (safe with several workers, also across hosts on Postgres).
    pub fn claim_queued(&self, worker: &str) -> Result<Option<(i64, String, String)>> {
        Ok(self
            .row(
                "UPDATE executions SET status = 'running', worker = ?1
                 WHERE status = 'queued' AND id = (SELECT id FROM executions WHERE status = 'queued' AND queue_payload IS NOT NULL ORDER BY id LIMIT 1 /*SKIP_LOCKED*/)
                 RETURNING id, workflow_data, queue_payload",
                p![worker],
            )?
            .map(|r| (r[0].i(), r[1].s(), r[2].s())))
    }

    pub fn status_of(&self, id: i64) -> Result<Option<String>> {
        Ok(self.row("SELECT status FROM executions WHERE id = ?1", p![id])?.map(|r| r[0].s()))
    }

    pub fn save_reply(&self, id: i64, status: u16, headers: &Value, body: &[u8]) -> Result<()> {
        self.exec(
            "INSERT INTO replies(exec_id, status, headers, body) VALUES(?1, ?2, ?3, ?4)
             ON CONFLICT(exec_id) DO UPDATE SET status = excluded.status, headers = excluded.headers, body = excluded.body",
            p![id, status, headers.to_string(), body],
        )?;
        Ok(())
    }

    pub fn take_reply(&self, id: i64) -> Result<Option<(u16, Value, Vec<u8>)>> {
        Ok(self.row("DELETE FROM replies WHERE exec_id = ?1 RETURNING status, headers, body", p![id])?.map(|r| (r[0].i() as u16, r[1].json(), r[2].bytes())))
    }

    /// Stores a parked execution (status "waiting") with its engine snapshot.
    pub fn park_execution(&self, id: i64, data: &Value, snapshot: &Value, kind: &str, resume_at: Option<i64>) -> Result<()> {
        self.exec("UPDATE executions SET status = 'waiting', data = ?2 WHERE id = ?1", p![id, data.to_string()])?;
        self.exec(
            "INSERT INTO waiting(exec_id, kind, resume_at, snapshot) VALUES(?1, ?2, ?3, ?4)
             ON CONFLICT(exec_id) DO UPDATE SET kind = excluded.kind, resume_at = excluded.resume_at, snapshot = excluded.snapshot",
            p![id, kind, resume_at, snapshot.to_string()],
        )?;
        Ok(())
    }

    /// Removes and returns the snapshot; None when it was already taken (no double resume).
    pub fn take_waiting(&self, id: i64) -> Result<Option<Value>> {
        Ok(self.row("DELETE FROM waiting WHERE exec_id = ?1 RETURNING snapshot", p![id])?.map(|r| r[0].json()))
    }

    /// Kind and Wait node name of a parked execution, without taking it.
    pub fn waiting_info(&self, id: i64) -> Result<Option<(String, String)>> {
        Ok(self.row("SELECT kind, snapshot FROM waiting WHERE exec_id = ?1", p![id])?.map(|r| (r[0].s(), r[1].json()["wait_node"].as_str().unwrap_or_default().to_string())))
    }

    pub fn due_waiting(&self, now: i64) -> Result<Vec<i64>> {
        Ok(self.rows("SELECT exec_id FROM waiting WHERE resume_at IS NOT NULL AND resume_at <= ?1", p![now])?.into_iter().map(|r| r[0].i()).collect())
    }

    pub fn set_status(&self, id: i64, status: &str) -> Result<()> {
        self.exec("UPDATE executions SET status = ?2 WHERE id = ?1", p![id, status])?;
        Ok(())
    }

    pub fn finish_execution(&self, id: i64, status: &str, data: &Value) -> Result<()> {
        self.exec("UPDATE executions SET status = ?2, stopped_at = ?3, data = ?4 WHERE id = ?1", p![id, status, now_iso(), data.to_string()])?;
        Ok(())
    }

    pub fn executions(&self, workflow_id: Option<&str>, status: Option<&str>, limit: i64, before: Option<i64>) -> Result<Vec<Value>> {
        let rows = self.rows(
            "SELECT id, workflow_id, workflow_name, mode, status, started_at, stopped_at, retry_of, worker FROM executions
             WHERE (CAST(?1 AS TEXT) IS NULL OR workflow_id = ?1) AND (CAST(?2 AS TEXT) IS NULL OR status = ?2) AND (CAST(?3 AS INTEGER) IS NULL OR id < ?3)
             ORDER BY id DESC LIMIT ?4",
            p![workflow_id, status, before, limit],
        )?;
        Ok(rows
            .into_iter()
            .map(|r| {
                json!({"id": r[0].i().to_string(), "workflowId": r[1].os(), "workflowName": r[2].os(), "mode": r[3].s(),
                    "status": r[4].s(), "startedAt": r[5].s(), "stoppedAt": r[6].os(), "retryOf": r[7].oi(),
                    "finished": r[4].s() == "success", "worker": r[8].os()})
            })
            .collect())
    }

    pub fn execution(&self, id: i64) -> Result<Option<Value>> {
        Ok(self
            .row("SELECT id, workflow_id, workflow_name, mode, status, started_at, stopped_at, data, workflow_data, retry_of FROM executions WHERE id = ?1", p![id])?
            .map(|r| {
                json!({"id": r[0].i().to_string(), "workflowId": r[1].os(), "workflowName": r[2].os(), "mode": r[3].s(),
                    "status": r[4].s(), "startedAt": r[5].s(), "stoppedAt": r[6].os(), "data": r[7].json(),
                    "workflowData": r[8].json(), "retryOf": r[9].oi(), "finished": r[4].s() == "success"})
            }))
    }

    pub fn delete_execution(&self, id: i64) -> Result<()> {
        self.exec("DELETE FROM executions WHERE id = ?1", p![id])?;
        Ok(())
    }

    /// Drops executions older than `max_age_hours` and keeps at most `max_count`.
    pub fn prune_executions(&self, max_age_hours: i64, max_count: i64) -> Result<usize> {
        let cutoff = (chrono::Utc::now() - chrono::Duration::hours(max_age_hours)).to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        let a = self.exec("DELETE FROM executions WHERE started_at < ?1 AND status != 'running'", p![cutoff])?;
        let b = self.exec("DELETE FROM executions WHERE id <= (SELECT id FROM executions ORDER BY id DESC LIMIT 1 OFFSET ?1)", p![max_count])?;
        Ok(a + b)
    }

    /// Executions left in "running" by a crash of this main process can never finish.
    pub fn mark_crashed(&self) -> Result<()> {
        self.exec("UPDATE executions SET status = 'crashed', stopped_at = ?1 WHERE status = 'running' AND worker IS NULL", p![now_iso()])?;
        Ok(())
    }

    // ---- variables ----
    pub fn variables(&self) -> Result<Vec<Value>> {
        Ok(self
            .rows("SELECT id, key, value FROM variables ORDER BY key", &[])?
            .into_iter()
            .map(|r| json!({"id": r[0].s(), "key": r[1].s(), "value": r[2].s(), "type": "string"}))
            .collect())
    }
}

mod lite {
    use super::{Row, V};
    use anyhow::Result;
    use rusqlite::types::{ToSqlOutput, Value as SV, ValueRef};

    impl rusqlite::ToSql for V {
        fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
            Ok(ToSqlOutput::Owned(match self {
                V::Null => SV::Null,
                V::I(i) => SV::Integer(*i),
                V::F(f) => SV::Real(*f),
                V::T(s) => SV::Text(s.clone()),
                V::B(b) => SV::Blob(b.clone()),
            }))
        }
    }

    pub fn run(c: &rusqlite::Connection, sql: &str, params: &[V]) -> Result<(usize, Vec<Row>)> {
        let mut st = c.prepare_cached(sql)?;
        let cols = st.column_count();
        if cols == 0 {
            return Ok((st.execute(rusqlite::params_from_iter(params))?, vec![]));
        }
        let mut rows = st.query(rusqlite::params_from_iter(params))?;
        let mut out = vec![];
        while let Some(r) = rows.next()? {
            out.push(
                (0..cols)
                    .map(|i| match r.get_ref(i) {
                        Ok(ValueRef::Integer(i)) => V::I(i),
                        Ok(ValueRef::Real(f)) => V::F(f),
                        Ok(ValueRef::Text(t)) => V::T(String::from_utf8_lossy(t).into_owned()),
                        Ok(ValueRef::Blob(b)) => V::B(b.to_vec()),
                        _ => V::Null,
                    })
                    .collect(),
            );
        }
        Ok((out.len(), out))
    }
}

mod pg {
    use super::{Row, V};
    use anyhow::{Result, anyhow};
    use serde_json::Value;
    use std::collections::HashMap;
    use std::sync::mpsc;
    use tokio_postgres::types::{ToSql, Type};

    type Reply = mpsc::Sender<Result<(usize, Vec<Row>)>>;

    /// One connection owned by a dedicated thread, so every caller (async handlers, engine
    /// threads, workers) can use the database synchronously like SQLite.
    pub struct Pg(std::sync::Mutex<mpsc::Sender<(String, Vec<V>, Reply)>>);

    impl Pg {
        pub fn start(cfg: Value) -> Result<Pg> {
            let (tx, rx) = mpsc::channel::<(String, Vec<V>, Reply)>();
            let (ready_tx, ready_rx) = mpsc::channel::<Result<()>>();
            std::thread::Builder::new().name("postgres".into()).spawn(move || {
                let rt = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
                    Ok(rt) => rt,
                    Err(e) => return drop(ready_tx.send(Err(e.into()))),
                };
                rt.block_on(async move {
                    let mut client = match connect(&cfg).await {
                        Ok(c) => {
                            let _ = ready_tx.send(Ok(()));
                            c
                        }
                        Err(e) => return drop(ready_tx.send(Err(e))),
                    };
                    let mut cache: HashMap<String, tokio_postgres::Statement> = HashMap::new();
                    while let Ok((sql, params, reply)) = rx.recv() {
                        let mut res = run(&client, &mut cache, &sql, &params).await;
                        // A dropped connection (server restart) is re-opened once.
                        if client.is_closed() {
                            cache.clear();
                            if let Ok(c) = connect(&cfg).await {
                                client = c;
                                res = run(&client, &mut cache, &sql, &params).await;
                            }
                        }
                        let _ = reply.send(res);
                    }
                });
            })?;
            ready_rx.recv().map_err(|_| anyhow!("database thread died"))??;
            Ok(Pg(std::sync::Mutex::new(tx)))
        }

        pub fn run(&self, sql: String, params: Vec<V>) -> Result<(usize, Vec<Row>)> {
            let (tx, rx) = mpsc::channel();
            self.0.lock().map_err(|_| anyhow!("db lock poisoned"))?.send((sql, params, tx)).map_err(|_| anyhow!("database thread stopped"))?;
            rx.recv().map_err(|_| anyhow!("database thread stopped"))?
        }
    }

    async fn connect(cfg: &Value) -> Result<tokio_postgres::Client> {
        let client = crate::nodes::integrations::pg_connect(cfg).await?;
        let schema = cfg["schema"].as_str().unwrap_or("public").replace('"', "");
        client.batch_execute(&format!("CREATE SCHEMA IF NOT EXISTS \"{schema}\"; SET search_path TO \"{schema}\"")).await?;
        Ok(client)
    }

    /// Converts a value to the type the server expects for that parameter.
    fn param(v: &V, t: &Type) -> Box<dyn ToSql + Sync + Send> {
        match (*t == Type::INT8, *t == Type::INT4, *t == Type::INT2, *t == Type::FLOAT8 || *t == Type::FLOAT4, *t == Type::BYTEA, *t == Type::BOOL) {
            (true, ..) => Box::new(v.oi()),
            (_, true, ..) => Box::new(v.oi().map(|i| i as i32)),
            (_, _, true, ..) => Box::new(v.oi().map(|i| i as i16)),
            (_, _, _, true, ..) => Box::new(match v {
                V::Null => None,
                V::F(f) => Some(*f),
                other => Some(other.i() as f64),
            }),
            (_, _, _, _, true, _) => Box::new((!matches!(v, V::Null)).then(|| v.bytes())),
            (_, _, _, _, _, true) => Box::new(v.oi().map(|i| i != 0)),
            _ => Box::new(v.os()),
        }
    }

    fn cell(r: &tokio_postgres::Row, i: usize) -> V {
        let t = r.columns()[i].type_().clone();
        let v = if t == Type::INT8 {
            r.try_get::<_, Option<i64>>(i).ok().flatten().map(V::I)
        } else if t == Type::INT4 {
            r.try_get::<_, Option<i32>>(i).ok().flatten().map(|x| V::I(x as i64))
        } else if t == Type::INT2 {
            r.try_get::<_, Option<i16>>(i).ok().flatten().map(|x| V::I(x as i64))
        } else if t == Type::FLOAT8 {
            r.try_get::<_, Option<f64>>(i).ok().flatten().map(V::F)
        } else if t == Type::BOOL {
            r.try_get::<_, Option<bool>>(i).ok().flatten().map(|b| V::I(b as i64))
        } else if t == Type::BYTEA {
            r.try_get::<_, Option<Vec<u8>>>(i).ok().flatten().map(V::B)
        } else {
            r.try_get::<_, Option<String>>(i).ok().flatten().map(V::T)
        };
        v.unwrap_or(V::Null)
    }

    async fn run(client: &tokio_postgres::Client, cache: &mut HashMap<String, tokio_postgres::Statement>, sql: &str, params: &[V]) -> Result<(usize, Vec<Row>)> {
        let st = match cache.get(sql) {
            Some(s) => s.clone(),
            None => {
                let s = client.prepare(sql).await.map_err(pg_err)?;
                cache.insert(sql.to_string(), s.clone());
                s
            }
        };
        let boxed: Vec<Box<dyn ToSql + Sync + Send>> = params.iter().zip(st.params()).map(|(v, t)| param(v, t)).collect();
        let refs: Vec<&(dyn ToSql + Sync)> = boxed.iter().map(|b| b.as_ref() as &(dyn ToSql + Sync)).collect();
        if st.columns().is_empty() {
            return Ok((client.execute(&st, &refs).await.map_err(pg_err)? as usize, vec![]));
        }
        let rows = client.query(&st, &refs).await.map_err(pg_err)?;
        let out: Vec<Row> = rows.iter().map(|r| (0..r.len()).map(|i| cell(r, i)).collect()).collect();
        Ok((out.len(), out))
    }

    fn pg_err(e: tokio_postgres::Error) -> anyhow::Error {
        match e.as_db_error() {
            Some(d) => anyhow!("Postgres: {}", d.message()),
            None => anyhow!("Postgres: {e}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workflow_roundtrip_and_prune() {
        let db = Db::open(":memory:").unwrap();
        let mut w = Workflow { id: "w1".into(), name: "Test".into(), ..Default::default() };
        w.created_at = now_iso();
        w.updated_at = now_iso();
        db.save_workflow(&w).unwrap();
        assert_eq!(db.workflow("w1").unwrap().unwrap().name, "Test");
        for _ in 0..5 {
            let id = db.create_execution(&w, "manual", None).unwrap();
            db.finish_execution(id, "success", &json!({})).unwrap();
        }
        db.prune_executions(1000, 2).unwrap();
        assert_eq!(db.executions(None, None, 100, None).unwrap().len(), 2);
        db.save_reply(9, 201, &json!({"a": 1}), b"hi").unwrap();
        assert_eq!(db.take_reply(9).unwrap(), Some((201, json!({"a": 1}), b"hi".to_vec())));
        assert_eq!(db.take_reply(9).unwrap(), None);
    }

    #[test]
    fn translates_to_postgres() {
        assert_eq!(pg_sql("SELECT ?1, ?12 FROM t WHERE x = '?'"), "SELECT $1, $12 FROM t WHERE x = '?'");
        assert_eq!(pg_sql("id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER, b BLOB"), "id BIGSERIAL PRIMARY KEY, n BIGINT, b BYTEA");
        assert!(pg_sql("LIMIT 1 /*SKIP_LOCKED*/").ends_with("FOR UPDATE SKIP LOCKED"));
    }
}
