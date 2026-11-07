//! Shared application state and configuration (read from environment variables).
//! N8N_* names are accepted as aliases so existing deployments keep working.

use crate::db::Db;
use crate::security::Cipher;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, RwLock};
use tokio::sync::broadcast;

pub type AppState = Arc<App>;

#[derive(Clone, Debug)]
pub struct Config {
    pub port: u16,
    pub data_dir: String,
    pub base_url: String,
    pub files_dir: String,
    pub enable_execute_command: bool,
    pub block_env: bool,
    pub code_timeout_ms: i64,
    pub max_age_hours: i64,
    pub max_count: i64,
    pub timezone: String,
    /// Max. parallel production executions; None = unlimited.
    pub concurrency: Option<usize>,
    /// EXECUTIONS_MODE=queue: production executions are run by `flowwolf worker` processes.
    pub queue_mode: bool,
    pub worker_concurrency: usize,
}

fn env(keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|k| std::env::var(k).ok()).filter(|v| !v.is_empty())
}

fn flag(keys: &[&str]) -> bool {
    env(keys).is_some_and(|v| v == "true" || v == "1")
}

impl Config {
    pub fn from_env() -> Config {
        let port = env(&["FLOWWOLF_PORT", "N8N_PORT", "PORT"]).and_then(|p| p.parse().ok()).unwrap_or(5678);
        let data_dir = env(&["FLOWWOLF_DATA_DIR", "N8N_USER_FOLDER"]).unwrap_or_else(|| "./data".into());
        let base_url = env(&["FLOWWOLF_URL", "WEBHOOK_URL", "N8N_EDITOR_BASE_URL"])
            .unwrap_or_else(|| format!("http://localhost:{port}"))
            .trim_end_matches('/')
            .to_string();
        Config {
            port,
            files_dir: env(&["FLOWWOLF_FILES_DIR", "N8N_RESTRICT_FILE_ACCESS_TO"]).unwrap_or_else(|| format!("{data_dir}/files")),
            data_dir,
            base_url,
            enable_execute_command: flag(&["FLOWWOLF_ENABLE_EXECUTE_COMMAND"]),
            block_env: flag(&["FLOWWOLF_BLOCK_ENV_ACCESS", "N8N_BLOCK_ENV_ACCESS_IN_NODE"]),
            code_timeout_ms: env(&["FLOWWOLF_CODE_TIMEOUT_MS"]).and_then(|v| v.parse().ok()).unwrap_or(60_000),
            max_age_hours: env(&["FLOWWOLF_EXECUTIONS_MAX_AGE_HOURS", "EXECUTIONS_DATA_MAX_AGE"]).and_then(|v| v.parse().ok()).unwrap_or(336),
            max_count: env(&["FLOWWOLF_EXECUTIONS_MAX_COUNT", "EXECUTIONS_DATA_PRUNE_MAX_COUNT"]).and_then(|v| v.parse().ok()).unwrap_or(10_000),
            timezone: env(&["GENERIC_TIMEZONE", "TZ"]).unwrap_or_else(|| "UTC".into()),
            concurrency: env(&["FLOWWOLF_CONCURRENCY_PRODUCTION_LIMIT", "N8N_CONCURRENCY_PRODUCTION_LIMIT"]).and_then(|v| v.parse::<i64>().ok()).filter(|n| *n > 0).map(|n| n as usize),
            queue_mode: env(&["FLOWWOLF_EXECUTIONS_MODE", "EXECUTIONS_MODE"]).is_some_and(|m| m == "queue"),
            worker_concurrency: env(&["FLOWWOLF_WORKER_CONCURRENCY"]).and_then(|v| v.parse().ok()).filter(|n: &usize| *n > 0).unwrap_or(10),
        }
    }
}

#[derive(Clone)]
pub struct HookTarget {
    pub workflow_id: String,
    pub node: String,
}

/// A webhook registered by the editor while it waits for a test event.
pub struct TestHook {
    pub wf: crate::model::Workflow,
    pub node: String,
    pub notify: tokio::sync::mpsc::Sender<i64>,
}

pub struct App {
    pub cfg: Config,
    pub db: Db,
    pub cipher: Cipher,
    pub http: reqwest::Client,
    pub http_insecure: reqwest::Client,
    pub events: broadcast::Sender<Value>,
    pub running: Mutex<HashMap<i64, Arc<AtomicBool>>>,
    /// (METHOD or "FORM", path) -> target. Method "*" matches any.
    pub webhooks: RwLock<HashMap<(String, String), HookTarget>>,
    pub test_hooks: Mutex<HashMap<(String, String), TestHook>>,
    pub triggers: Mutex<HashMap<String, Vec<tokio::task::JoinHandle<()>>>>,
    /// OAuth2 state -> (credential id, created at ms, PKCE code verifier)
    pub oauth_states: Mutex<HashMap<String, (String, i64, Option<String>)>>,
    /// Main runtime: executions may call `launch` from their own short-lived runtime.
    pub rt: tokio::runtime::Handle,
    pub prod_slots: Option<Arc<tokio::sync::Semaphore>>,
    /// Name of this worker process; None for the main process.
    pub worker: Option<String>,
}

impl App {
    pub fn push(&self, v: Value) {
        let _ = self.events.send(v);
    }
}
