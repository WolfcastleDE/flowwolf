//! Source control: push/pull workflows, credential stubs and variable keys to a Git repository.
//! Two transports: the GitHub or Gitea/Forgejo contents API (token), or plain Git over SSH with a
//! deploy key that Flowwolf generates (cargo feature "git-ssh", libgit2 + libssh2 linked in).
//! Layout follows n8n: workflows/<id>.json, credential_stubs/<id>.json, variable_stubs.json.

use crate::app::App;
use crate::model::*;
use anyhow::{Result, anyhow};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use serde_json::{Value, json};
use std::time::Duration;

pub const PREFS_KEY: &str = "sourceControl";
pub const TOKEN_KEY: &str = "sourceControlToken";
/// Encrypted OpenSSH private key used for the SSH transport.
pub const SSH_KEY: &str = "sourceControlSshKey";

/// A file in the repository, relative to the configured folder.
type File = (String, Vec<u8>);

fn prefs(app: &App) -> Result<Value> {
    app.db.setting(PREFS_KEY)?.and_then(|s| serde_json::from_str(&s).ok()).ok_or_else(|| anyhow!("Source control is not configured"))
}

fn folder_of(p: &Value) -> String {
    as_str(&p["folder"]).trim_matches('/').to_string()
}

fn branch_of(p: &Value) -> String {
    p["branch"].as_str().filter(|b| !b.is_empty()).unwrap_or("main").to_string()
}

// ---------------------------------------------------------------------------
// HTTP contents API (GitHub, Gitea/Forgejo)
// ---------------------------------------------------------------------------

struct Repo<'a> {
    app: &'a App,
    api: String,
    repo: String,
    branch: String,
    folder: String,
    token: String,
    gitea: bool,
}

impl<'a> Repo<'a> {
    fn open(app: &'a App, p: &Value) -> Result<Repo<'a>> {
        let token = app.db.setting(TOKEN_KEY)?.map(|c| app.cipher.decrypt(&c)).transpose()?.map(|v| as_str(&v)).unwrap_or_default();
        let gitea = p["provider"] == "gitea";
        let api = p["apiUrl"].as_str().filter(|s| !s.is_empty()).unwrap_or(if gitea { "" } else { "https://api.github.com" }).trim_end_matches('/').to_string();
        if api.is_empty() || as_str(&p["repository"]).is_empty() {
            return Err(anyhow!("API URL and repository are required"));
        }
        Ok(Repo { app, api, repo: as_str(&p["repository"]), branch: branch_of(p), folder: folder_of(p), token, gitea })
    }

    fn path(&self, p: &str) -> String {
        if self.folder.is_empty() { p.to_string() } else { format!("{}/{p}", self.folder) }
    }

    fn req(&self, m: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let url = format!("{}/repos/{}/contents/{}", self.api, self.repo, self.path(path));
        let mut rb = self.app.http.request(m, url).timeout(Duration::from_secs(60)).header("accept", "application/json");
        if !self.token.is_empty() {
            rb = rb.header("authorization", format!("token {}", self.token));
        }
        rb
    }

    async fn get(&self, path: &str) -> Result<Option<Value>> {
        let r = self.req(reqwest::Method::GET, path).query(&[("ref", &self.branch)]).send().await?;
        if r.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let status = r.status();
        let v: Value = r.json().await?;
        if !status.is_success() {
            return Err(anyhow!("Git provider returned {status}: {}", as_str(&v["message"])));
        }
        Ok(Some(v))
    }

    /// (sha, content) of a file.
    async fn read(&self, path: &str) -> Result<Option<(String, Vec<u8>)>> {
        let Some(v) = self.get(path).await? else { return Ok(None) };
        let b64: String = as_str(&v["content"]).chars().filter(|c| !c.is_whitespace()).collect();
        Ok(Some((as_str(&v["sha"]), B64.decode(b64)?)))
    }

    async fn list(&self, dir: &str) -> Result<Vec<String>> {
        Ok(match self.get(dir).await? {
            Some(Value::Array(a)) => a.iter().filter(|e| e["type"] == "file").map(|e| as_str(&e["name"])).collect(),
            _ => vec![],
        })
    }

    /// Writes a file when its content changed. Returns true if a commit was made.
    async fn write(&self, path: &str, content: &[u8], message: &str) -> Result<bool> {
        let existing = self.read(path).await?;
        if existing.as_ref().is_some_and(|(_, c)| c == content) {
            return Ok(false);
        }
        let mut body = json!({"message": message, "content": B64.encode(content), "branch": self.branch});
        let method = match &existing {
            Some((sha, _)) => {
                body["sha"] = json!(sha);
                reqwest::Method::PUT
            }
            // Gitea creates files with POST, GitHub with PUT.
            None if self.gitea => reqwest::Method::POST,
            None => reqwest::Method::PUT,
        };
        let r = self.req(method, path).json(&body).send().await?;
        if !r.status().is_success() {
            let status = r.status();
            let v: Value = r.json().await.unwrap_or_default();
            return Err(anyhow!("Writing {path} failed ({status}): {}", as_str(&v["message"])));
        }
        Ok(true)
    }

    async fn read_all(&self) -> Result<Vec<File>> {
        let mut out = vec![];
        for dir in ["workflows", "credential_stubs"] {
            for name in self.list(dir).await? {
                let p = format!("{dir}/{name}");
                if let Some((_, b)) = self.read(&p).await? {
                    out.push((p, b));
                }
            }
        }
        if let Some((_, b)) = self.read("variable_stubs.json").await? {
            out.push(("variable_stubs.json".into(), b));
        }
        Ok(out)
    }

    async fn branches(&self) -> Result<Vec<String>> {
        let mut rb = self.app.http.get(format!("{}/repos/{}/branches", self.api, self.repo)).query(&[("per_page", "100"), ("limit", "50")]).header("accept", "application/json");
        if !self.token.is_empty() {
            rb = rb.header("authorization", format!("token {}", self.token));
        }
        let r = rb.send().await?;
        let status = r.status();
        let v: Value = r.json().await?;
        if !status.is_success() {
            return Err(anyhow!("Git provider returned {status}: {}", as_str(&v["message"])));
        }
        Ok(v.as_array().into_iter().flatten().map(|b| as_str(&b["name"])).collect())
    }
}

// ---------------------------------------------------------------------------
// Git over SSH
// ---------------------------------------------------------------------------

#[cfg(feature = "git-ssh")]
pub mod ssh {
    use super::File;
    use anyhow::{Result, anyhow};
    use git2::{CertificateCheckStatus, Cred, FetchOptions, IndexAddOption, PushOptions, RemoteCallbacks, Repository, ResetType, Signature};
    use std::cell::{Cell, RefCell};
    use std::path::PathBuf;

    pub struct Cfg {
        pub url: String,
        pub branch: String,
        pub folder: String,
        pub key: String,
        /// Working copy, owned by Flowwolf (wiped and re-checked-out on every sync).
        pub dir: PathBuf,
        /// SHA-256 of the server host key seen before (trust on first use).
        pub host_key: Option<String>,
    }

    /// Generates an ed25519 deploy key. Returns (OpenSSH private key, public key line).
    pub fn generate_key() -> Result<(String, String)> {
        let mut k = ssh_key::PrivateKey::random(&mut ssh_key::rand_core::OsRng, ssh_key::Algorithm::Ed25519)?;
        k.set_comment("flowwolf");
        Ok((k.to_openssh(ssh_key::LineEnding::LF)?.to_string(), k.public_key().to_openssh()?))
    }

    /// Callbacks for authentication and host key pinning. `seen` receives the host key fingerprint.
    fn callbacks<'a>(cfg: &'a Cfg, seen: &'a RefCell<Option<String>>, tries: &'a Cell<u8>) -> RemoteCallbacks<'a> {
        let mut cb = RemoteCallbacks::new();
        cb.credentials(move |_url, user, _allowed| {
            // libgit2 asks again after a rejected key; stop instead of looping forever.
            tries.set(tries.get() + 1);
            if tries.get() > 2 {
                return Err(git2::Error::from_str("the server rejected the deploy key (is the public key added with write access?)"));
            }
            Cred::ssh_key_from_memory(user.unwrap_or("git"), None, &cfg.key, None)
        });
        cb.certificate_check(move |cert, host| {
            use base64::Engine as _;
            let hk = cert.as_hostkey().ok_or_else(|| git2::Error::from_str("not an SSH host key"))?;
            let fp = hk.hash_sha256().map(|h| base64::engine::general_purpose::STANDARD_NO_PAD.encode(h)).ok_or_else(|| git2::Error::from_str("host key without SHA-256 hash"))?;
            match &cfg.host_key {
                Some(known) if *known != fp => Err(git2::Error::from_str(&format!(
                    "the SSH host key of {host} changed (now SHA256:{fp}). If that is expected, reset the host key in the source control settings"
                ))),
                _ => {
                    *seen.borrow_mut() = Some(fp);
                    Ok(CertificateCheckStatus::CertificateOk)
                }
            }
        });
        cb
    }

    fn ctx(e: git2::Error) -> anyhow::Error {
        anyhow!("Git: {}", e.message())
    }

    /// Fetches the branch and resets the working copy to it. Returns the repository and the remote head.
    fn sync(cfg: &Cfg, seen: &RefCell<Option<String>>) -> Result<(Repository, Option<git2::Oid>)> {
        let reuse = Repository::open(&cfg.dir).ok().filter(|r| r.find_remote("origin").ok().and_then(|o| o.url().map(String::from)).as_deref() == Some(cfg.url.as_str()));
        let repo = match reuse {
            Some(r) => r,
            None => {
                let _ = std::fs::remove_dir_all(&cfg.dir);
                let r = Repository::init(&cfg.dir).map_err(ctx)?;
                r.remote("origin", &cfg.url).map_err(ctx)?;
                r
            }
        };
        {
            let tries = Cell::new(0);
            let mut fo = FetchOptions::new();
            fo.remote_callbacks(callbacks(cfg, seen, &tries));
            let mut remote = repo.find_remote("origin").map_err(ctx)?;
            remote.fetch(&["+refs/heads/*:refs/remotes/origin/*"], Some(&mut fo), None).map_err(ctx)?;
        }
        // Start from a clean tree: drop everything but .git, then check out the remote state.
        for e in std::fs::read_dir(&cfg.dir)?.flatten() {
            if e.file_name() != ".git" {
                let p = e.path();
                let _ = if p.is_dir() { std::fs::remove_dir_all(p) } else { std::fs::remove_file(p) };
            }
        }
        repo.set_head(&format!("refs/heads/{}", cfg.branch)).map_err(ctx)?;
        let head = repo.find_reference(&format!("refs/remotes/origin/{}", cfg.branch)).ok().and_then(|r| r.target());
        if let Some(oid) = head {
            let obj = repo.find_object(oid, None).map_err(ctx)?;
            repo.reset(&obj, ResetType::Hard, None).map_err(ctx)?;
        }
        Ok((repo, head))
    }

    fn rel(cfg: &Cfg, p: &str) -> String {
        if cfg.folder.is_empty() { p.to_string() } else { format!("{}/{p}", cfg.folder) }
    }

    /// Writes changed files, commits and pushes. Returns (changed paths, host key fingerprint).
    pub fn push(cfg: &Cfg, files: &[File], msg: &str) -> Result<(Vec<String>, Option<String>)> {
        let seen = RefCell::new(None);
        let (repo, head) = sync(cfg, &seen)?;
        let mut changed = vec![];
        for (p, bytes) in files {
            let full = cfg.dir.join(rel(cfg, p));
            if std::fs::read(&full).ok().as_deref() != Some(bytes.as_slice()) {
                if let Some(parent) = full.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::write(&full, bytes)?;
                changed.push(p.clone());
            }
        }
        if changed.is_empty() {
            return Ok((changed, seen.take()));
        }
        let mut idx = repo.index().map_err(ctx)?;
        idx.add_all(["*"].iter(), IndexAddOption::DEFAULT, None).map_err(ctx)?;
        idx.write().map_err(ctx)?;
        let tree = repo.find_tree(idx.write_tree().map_err(ctx)?).map_err(ctx)?;
        let sig = Signature::now("Flowwolf", "flowwolf@localhost").map_err(ctx)?;
        let parents: Vec<git2::Commit> = head.map(|h| repo.find_commit(h)).transpose().map_err(ctx)?.into_iter().collect();
        let parent_refs: Vec<&git2::Commit> = parents.iter().collect();
        let branch_ref = format!("refs/heads/{}", cfg.branch);
        repo.commit(Some(&branch_ref), &sig, &sig, msg, &tree, &parent_refs).map_err(ctx)?;
        let rejected = RefCell::new(None);
        {
            let tries = Cell::new(0);
            let mut cb = callbacks(cfg, &seen, &tries);
            cb.push_update_reference(|r, status| {
                if let Some(s) = status {
                    *rejected.borrow_mut() = Some(format!("{r}: {s}"));
                }
                Ok(())
            });
            let mut po = PushOptions::new();
            po.remote_callbacks(cb);
            repo.find_remote("origin").map_err(ctx)?.push(&[format!("{branch_ref}:{branch_ref}")], Some(&mut po)).map_err(ctx)?;
        }
        if let Some(e) = rejected.take() {
            return Err(anyhow!("The server rejected the push ({e}); pull first if someone else pushed"));
        }
        Ok((changed, seen.take()))
    }

    /// Reads every file of the branch below the configured folder.
    pub fn read_all(cfg: &Cfg) -> Result<(Vec<File>, Option<String>)> {
        let seen = RefCell::new(None);
        let (repo, head) = sync(cfg, &seen)?;
        let mut out = vec![];
        if let Some(h) = head {
            let tree = repo.find_commit(h).map_err(ctx)?.tree().map_err(ctx)?;
            let prefix = if cfg.folder.is_empty() { String::new() } else { format!("{}/", cfg.folder) };
            tree.walk(git2::TreeWalkMode::PreOrder, |root, entry| {
                if entry.kind() == Some(git2::ObjectType::Blob) {
                    let path = format!("{root}{}", entry.name().unwrap_or_default());
                    if let (Some(p), Ok(blob)) = (path.strip_prefix(&prefix), repo.find_blob(entry.id())) {
                        out.push((p.to_string(), blob.content().to_vec()));
                    }
                }
                git2::TreeWalkResult::Ok
            })
            .map_err(ctx)?;
        }
        Ok((out, seen.take()))
    }

    pub fn branches(cfg: &Cfg) -> Result<(Vec<String>, Option<String>)> {
        let seen = RefCell::new(None);
        let tries = Cell::new(0);
        let mut remote = git2::Remote::create_detached(cfg.url.as_str()).map_err(ctx)?;
        let names = {
            let conn = remote.connect_auth(git2::Direction::Fetch, Some(callbacks(cfg, &seen, &tries)), None).map_err(ctx)?;
            conn.list().map_err(ctx)?.iter().filter_map(|h| h.name().strip_prefix("refs/heads/").map(String::from)).collect()
        };
        Ok((names, seen.take()))
    }
}

#[cfg(feature = "git-ssh")]
fn ssh_cfg(app: &App, p: &Value) -> Result<ssh::Cfg> {
    let url = as_str(&p["repositoryUrl"]);
    if url.is_empty() {
        return Err(anyhow!("Repository URL is required (git@host:owner/repo.git)"));
    }
    let key = app.db.setting(SSH_KEY)?.map(|c| app.cipher.decrypt(&c)).transpose()?.map(|v| as_str(&v)).filter(|k| !k.is_empty());
    let key = key.ok_or_else(|| anyhow!("Generate an SSH key in the source control settings and add the public key to the repository first"))?;
    Ok(ssh::Cfg {
        url,
        branch: branch_of(p),
        folder: folder_of(p),
        key,
        dir: std::path::Path::new(&app.cfg.data_dir).join("git"),
        host_key: p["hostKey"].as_str().filter(|s| !s.is_empty()).map(String::from),
    })
}

/// Remembers the host key the first time a server is contacted.
#[cfg(feature = "git-ssh")]
fn pin_host_key(app: &App, fp: Option<String>) -> Result<()> {
    let mut p = prefs(app)?;
    if let (Some(fp), true) = (fp, as_str(&p["hostKey"]).is_empty()) {
        p["hostKey"] = json!(fp);
        app.db.set_setting(PREFS_KEY, &p.to_string())?;
    }
    Ok(())
}

/// Serialises Git operations: they share one working copy.
static LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
    tokio::task::spawn_blocking(f).await.map_err(|e| anyhow!("Git task failed: {e}"))?
}

fn is_ssh(p: &Value) -> bool {
    p["provider"] == "ssh"
}

#[cfg(not(feature = "git-ssh"))]
fn no_ssh<T>() -> Result<T> {
    Err(anyhow!("This build has no Git-over-SSH support (cargo feature \"git-ssh\")"))
}

fn pretty(v: &Value) -> Vec<u8> {
    let mut s = serde_json::to_vec_pretty(v).unwrap_or_default();
    s.push(b'\n');
    s
}

/// Branch names of the configured repository.
pub async fn branches(app: &App) -> Result<Vec<String>> {
    let p = prefs(app)?;
    if is_ssh(&p) {
        #[cfg(feature = "git-ssh")]
        {
            let cfg = ssh_cfg(app, &p)?;
            let (names, fp) = blocking(move || ssh::branches(&cfg)).await?;
            pin_host_key(app, fp)?;
            return Ok(names);
        }
        #[cfg(not(feature = "git-ssh"))]
        return no_ssh();
    }
    Repo::open(app, &p)?.branches().await
}

/// Everything that goes into the repository.
fn snapshot(app: &App) -> Result<Vec<File>> {
    let mut files = vec![];
    for w in app.db.workflows()? {
        let doc = json!({"id": w.id, "name": w.name, "active": w.active, "nodes": w.nodes, "connections": w.connections, "settings": w.settings, "tags": w.tags, "folder": w.folder});
        files.push((format!("workflows/{}.json", w.id), pretty(&doc)));
    }
    for c in app.db.credentials()? {
        let doc = json!({"id": c["id"], "name": c["name"], "type": c["type"], "data": {}});
        files.push((format!("credential_stubs/{}.json", as_str(&c["id"])), pretty(&doc)));
    }
    let vars: Vec<Value> = app.db.variables()?.into_iter().map(|v| json!({"id": v["id"], "key": v["key"], "value": "", "type": "string"})).collect();
    files.push(("variable_stubs.json".into(), pretty(&Value::Array(vars))));
    Ok(files)
}

/// Pushes all workflows, credential stubs (no secrets) and variable keys. Returns counts of changed files.
pub async fn push(app: &App, message: &str) -> Result<Value> {
    let _guard = LOCK.lock().await;
    let p = prefs(app)?;
    let msg = if message.trim().is_empty() { "Update from Flowwolf" } else { message.trim() }.to_string();
    let files = snapshot(app)?;
    let changed: Vec<String> = if is_ssh(&p) {
        #[cfg(feature = "git-ssh")]
        {
            let cfg = ssh_cfg(app, &p)?;
            let (changed, fp) = blocking(move || ssh::push(&cfg, &files, &msg)).await?;
            pin_host_key(app, fp)?;
            changed
        }
        #[cfg(not(feature = "git-ssh"))]
        return no_ssh();
    } else {
        let repo = Repo::open(app, &p)?;
        let mut changed = vec![];
        for (path, bytes) in &files {
            if repo.write(path, bytes, &msg).await? {
                changed.push(path.clone());
            }
        }
        changed
    };
    let count = |prefix: &str| changed.iter().filter(|c| c.starts_with(prefix)).count();
    Ok(json!({"workflows": count("workflows/"), "credentials": count("credential_stubs/"), "variables": count("variable_stubs")}))
}

/// Pulls workflows (created or updated, active ones are re-activated), missing credential stubs and variable keys.
pub async fn pull(app: &crate::app::AppState, user: &str) -> Result<Value> {
    let _guard = LOCK.lock().await;
    let p = prefs(app)?;
    let files: Vec<File> = if is_ssh(&p) {
        #[cfg(feature = "git-ssh")]
        {
            let cfg = ssh_cfg(app, &p)?;
            let (files, fp) = blocking(move || ssh::read_all(&cfg)).await?;
            pin_host_key(app, fp)?;
            files
        }
        #[cfg(not(feature = "git-ssh"))]
        return no_ssh();
    } else {
        Repo::open(app, &p)?.read_all().await?
    };
    let in_dir = |dir: &str| -> Vec<(String, Vec<u8>)> {
        files
            .iter()
            .filter_map(|(p, b)| p.strip_prefix(dir).filter(|n| n.ends_with(".json") && !n.contains('/')).map(|n| (n.to_string(), b.clone())))
            .collect()
    };
    let (mut created, mut updated) = (0, 0);
    for (name, bytes) in in_dir("workflows/") {
        let doc: Value = serde_json::from_slice(&bytes).map_err(|e| anyhow!("workflows/{name}: {e}"))?;
        let mut w: Workflow = serde_json::from_value(doc)?;
        if w.id.is_empty() {
            w.id = name.trim_end_matches(".json").to_string();
        }
        match app.db.workflow(&w.id)? {
            Some(old) => {
                // Keep local state that is not part of the file.
                w.active = old.active;
                w.created_at = old.created_at;
                w.static_data = old.static_data;
                w.pin_data = old.pin_data;
                w.updated_at = now_iso();
                app.db.save_workflow(&w)?;
                if w.active {
                    if let Err(e) = crate::triggers::activate(app, &w) {
                        tracing::error!("re-activating {} after pull failed: {e}", w.name);
                    }
                }
                updated += 1;
            }
            None => {
                w.active = false;
                w.created_at = now_iso();
                w.updated_at = w.created_at.clone();
                app.db.save_workflow(&w)?;
                app.db.set_owner("workflows", &w.id, user)?;
                created += 1;
            }
        }
    }
    let mut creds = 0;
    for (_, bytes) in in_dir("credential_stubs/") {
        let c: Value = serde_json::from_slice(&bytes)?;
        let (id, cname, kind) = (as_str(&c["id"]), as_str(&c["name"]), as_str(&c["type"]));
        if id.is_empty() || app.db.credential(&id)?.is_some() || app.db.credential_by_name(&cname, &kind)?.is_some() {
            continue;
        }
        app.db.save_credential(&id, &cname, &kind, &app.cipher.encrypt(&json!({}))?)?;
        app.db.set_owner("credentials", &id, user)?;
        creds += 1;
    }
    let mut vars = 0;
    if let Some((_, bytes)) = files.iter().find(|(p, _)| p == "variable_stubs.json") {
        let existing: Vec<String> = app.db.variables()?.iter().map(|v| as_str(&v["key"])).collect();
        for v in serde_json::from_slice::<Vec<Value>>(bytes).unwrap_or_default() {
            let key = as_str(&v["key"]);
            if !key.is_empty() && !existing.contains(&key) {
                app.db.exec("INSERT INTO variables(id, key, value) VALUES(?1, ?2, '')", crate::p![short_id(), &key])?;
                vars += 1;
            }
        }
    }
    Ok(json!({"workflowsCreated": created, "workflowsUpdated": updated, "credentialStubs": creds, "variables": vars}))
}

/// Creates a new deploy key and returns its public half (the private key is stored encrypted).
pub fn generate_key(app: &App) -> Result<String> {
    #[cfg(feature = "git-ssh")]
    {
        let (private, public) = ssh::generate_key()?;
        app.db.set_setting(SSH_KEY, &app.cipher.encrypt(&json!(private))?)?;
        let mut p: Value = app.db.setting(PREFS_KEY)?.and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}));
        p["publicKey"] = json!(public);
        app.db.set_setting(PREFS_KEY, &p.to_string())?;
        Ok(public)
    }
    #[cfg(not(feature = "git-ssh"))]
    {
        let _ = app;
        no_ssh()
    }
}
