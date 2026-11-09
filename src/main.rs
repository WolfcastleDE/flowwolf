//! Flowwolf: a fast, lightweight workflow automation server compatible with n8n workflows.

mod api;
mod app;
mod db;
mod engine;
mod gitsync;
mod hooktriggers;
mod imap;
mod listeners;
mod pollers;
mod js;
mod model;
mod nodes;
mod oauth;
#[cfg(feature = "python")]
mod python;
mod security;
mod triggers;
mod updates;
mod users;

use anyhow::Result;
use app::{App, Config};
use std::sync::Arc;
use std::time::Duration;

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("healthcheck") {
        // Used by the Docker HEALTHCHECK; the image ships without curl.
        let port = Config::from_env().port;
        let ok = std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_secs(3)).is_ok();
        std::process::exit(if ok { 0 } else { 1 });
    }
    #[cfg(feature = "python")]
    if args.get(1).map(String::as_str) == Some("python-sandbox") {
        python::sandbox_main();
    }
    // QuickJS and libc read TZ for local time; set it before any thread starts.
    if let (Ok(tz), Err(_)) = (std::env::var("GENERIC_TIMEZONE"), std::env::var("TZ")) {
        unsafe { std::env::set_var("TZ", tz) };
    }
    let worker = args.get(1).map(String::as_str) == Some("worker");
    tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(serve(worker))
}

fn encryption_key(cfg: &Config) -> Result<String> {
    if let Some(k) = ["FLOWWOLF_ENCRYPTION_KEY", "N8N_ENCRYPTION_KEY"].iter().find_map(|k| std::env::var(k).ok()).filter(|k| !k.is_empty()) {
        return Ok(k);
    }
    let path = format!("{}/encryption.key", cfg.data_dir);
    if let Ok(k) = std::fs::read_to_string(&path) {
        return Ok(k.trim().to_string());
    }
    let k = security::random_token();
    std::fs::write(&path, &k)?;
    tracing::warn!("generated a new encryption key in {path}; back it up or set FLOWWOLF_ENCRYPTION_KEY");
    Ok(k)
}

async fn serve(worker: bool) -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info,tower_http=warn".into()))
        .init();
    let cfg = Config::from_env();
    std::fs::create_dir_all(&cfg.data_dir)?;
    let db = db::Db::from_env(&cfg.data_dir)?;
    if !worker {
        db.mark_crashed()?;
    }
    let ua = concat!("flowwolf/", env!("CARGO_PKG_VERSION"));
    let app = Arc::new(App {
        cipher: security::Cipher::new(&encryption_key(&cfg)?),
        http: reqwest::Client::builder().user_agent(ua).build()?,
        http_insecure: reqwest::Client::builder().user_agent(ua).danger_accept_invalid_certs(true).build()?,
        events: tokio::sync::broadcast::channel(1024).0,
        running: Default::default(),
        webhooks: Default::default(),
        test_hooks: Default::default(),
        triggers: Default::default(),
        oauth_states: Default::default(),
        rt: tokio::runtime::Handle::current(),
        prod_slots: cfg.concurrency.map(|n| Arc::new(tokio::sync::Semaphore::new(n))),
        worker: worker.then(|| std::env::var("HOSTNAME").unwrap_or_else(|_| format!("worker-{}", std::process::id()))),
        db,
        cfg: cfg.clone(),
    });

    if worker {
        // Workers only execute; triggers, webhooks and the editor stay in the main process.
        tokio::select! { _ = engine::worker_loop(app) => {}, _ = shutdown() => {} }
        return Ok(());
    }
    if cfg.queue_mode {
        tracing::info!("queue mode: production executions are run by `flowwolf worker` processes");
    }
    for wf in app.db.workflows()?.into_iter().filter(|w| w.active) {
        match triggers::activate(&app, &wf) {
            Ok(()) => tracing::info!("activated workflow \"{}\"", wf.name),
            Err(e) => tracing::error!("could not activate workflow \"{}\": {e}", wf.name),
        }
    }

    tokio::spawn(engine::resume_due(app.clone()));
    tokio::spawn(updates::run(app.clone()));
    let a = app.clone();
    tokio::spawn(async move {
        loop {
            match a.db.prune_executions(a.cfg.max_age_hours, a.cfg.max_count) {
                Ok(n) if n > 0 => tracing::info!("pruned {n} old executions"),
                Err(e) => tracing::error!("pruning failed: {e}"),
                _ => {}
            }
            tokio::time::sleep(Duration::from_secs(3600)).await;
        }
    });

    let running = app.clone();
    let router = api::router(app).layer(tower_http::compression::CompressionLayer::new());
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", cfg.port)).await?;
    tracing::info!("Flowwolf {} listening on {} (editor: {}/app)", env!("CARGO_PKG_VERSION"), cfg.port, cfg.base_url);
    axum::serve(listener, router).with_graceful_shutdown(shutdown()).await?;
    // Let running executions finish before the process ends (n8n: N8N_GRACEFUL_SHUTDOWN_TIMEOUT).
    let secs: u64 = ["FLOWWOLF_GRACEFUL_SHUTDOWN_TIMEOUT", "N8N_GRACEFUL_SHUTDOWN_TIMEOUT"].iter()
        .find_map(|k| std::env::var(k).ok()).and_then(|v| v.parse().ok()).unwrap_or(30);
    let deadline = std::time::Instant::now() + Duration::from_secs(secs);
    let left = || running.running.lock().unwrap().len();
    if left() > 0 {
        tracing::info!("waiting up to {secs} s for {} running execution(s)", left());
    }
    while left() > 0 && std::time::Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    if left() > 0 {
        tracing::warn!("stopping with {} execution(s) still running; they are marked as interrupted on the next start", left());
    }
    Ok(())
}

async fn shutdown() {
    let ctrl_c = tokio::signal::ctrl_c();
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("signal handler");
        tokio::select! { _ = ctrl_c => {}, _ = term.recv() => {} }
    }
    #[cfg(not(unix))]
    let _ = ctrl_c.await;
    tracing::info!("shutting down");
}
