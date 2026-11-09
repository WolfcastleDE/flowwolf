//! Update notices and the update button.
//!
//! Once a day (and on start) the server asks GitHub for the Flowwolf releases and remembers the ones
//! newer than the running version. The editor shows them in the sidebar and under Settings → About.
//! Owners and admins can install an update when `FLOWWOLF_UPDATE_WEBHOOK` is set: Flowwolf then calls
//! that deploy hook (e.g. the deploy webhook of a Dokploy or Coolify app), which pulls or builds the
//! new version and replaces the container. A container cannot safely replace itself, hence the hook.
//!
//! Settings: `FLOWWOLF_VERSION_NOTIFICATIONS_ENABLED` / `N8N_VERSION_NOTIFICATIONS_ENABLED` (default on),
//! `FLOWWOLF_UPDATE_URL` (releases API, default GitHub), `FLOWWOLF_UPDATE_WEBHOOK` (deploy hook).

use crate::api::{R, User, err, ok, require_owner};
use crate::app::AppState;
use axum::Extension;
use axum::extract::State;
use axum::http::StatusCode;
use serde_json::{Value, json};
use std::sync::{LazyLock, RwLock};
use std::time::Duration;

const RELEASES: &str = "https://api.github.com/repos/WolfcastleDE/flowwolf/releases?per_page=30";
const CURRENT: &str = env!("CARGO_PKG_VERSION");

/// Last check: newer releases (newest first), when it ran and the error if it failed.
static STATE: LazyLock<RwLock<Value>> = LazyLock::new(|| RwLock::new(json!({"releases": [], "checkedAt": null, "error": null})));

fn env(keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|k| std::env::var(k).ok()).filter(|v| !v.trim().is_empty())
}

fn enabled() -> bool {
    env(&["FLOWWOLF_VERSION_NOTIFICATIONS_ENABLED", "N8N_VERSION_NOTIFICATIONS_ENABLED"]).is_none_or(|v| v != "false" && v != "0")
}

/// "v1.2.3" / "1.2.3-rc.1" -> (1, 2, 3, is_prerelease)
fn parse(tag: &str) -> Option<(u64, u64, u64, bool)> {
    let t = tag.trim().trim_start_matches(['v', 'V']);
    let (core, pre) = match t.split_once(['-', '+']) {
        Some((c, _)) => (c, true),
        None => (t, false),
    };
    let mut it = core.split('.').map(|p| p.parse::<u64>());
    let (a, b, c) = (it.next()?.ok()?, it.next().unwrap_or(Ok(0)).ok()?, it.next().unwrap_or(Ok(0)).ok()?);
    Some((a, b, c, pre))
}

/// Releases newer than `current`, newest first; drafts and pre-releases are left out.
fn newer(list: &Value, current: &str) -> Vec<Value> {
    let Some((ca, cb, cc, _)) = parse(current) else { return vec![] };
    let mut out: Vec<((u64, u64, u64), Value)> = list.as_array().into_iter().flatten()
        .filter(|r| r["draft"] != true && r["prerelease"] != true)
        .filter_map(|r| {
            let tag = r["tag_name"].as_str()?;
            let (a, b, c, pre) = parse(tag)?;
            ((a, b, c) > (ca, cb, cc) && !pre).then(|| ((a, b, c), json!({
                "version": tag.trim_start_matches(['v', 'V']), "name": r["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(tag),
                "url": r["html_url"], "publishedAt": r["published_at"], "notes": r["body"].as_str().unwrap_or(""),
            })))
        })
        .collect();
    out.sort_by(|x, y| y.0.cmp(&x.0));
    out.into_iter().map(|(_, v)| v).collect()
}

/// Asks the releases API once and stores the result.
pub async fn check(app: &AppState) {
    let url = env(&["FLOWWOLF_UPDATE_URL"]).unwrap_or_else(|| RELEASES.into());
    let res = async {
        let r = app.http.get(&url).header("accept", "application/vnd.github+json").timeout(Duration::from_secs(20)).send().await?;
        if !r.status().is_success() {
            anyhow::bail!("release check answered {}", r.status());
        }
        Ok::<Value, anyhow::Error>(r.json().await?)
    }.await;
    let now = crate::model::now_iso();
    let mut s = STATE.write().unwrap();
    match res {
        Ok(list) => *s = json!({"releases": newer(&list, CURRENT), "checkedAt": now, "error": null}),
        Err(e) => {
            // Keep what we knew; only note the failure.
            s["checkedAt"] = json!(now);
            s["error"] = json!(e.to_string());
            tracing::debug!("update check failed: {e}");
        }
    }
}

/// Background loop: shortly after start, then once a day.
pub async fn run(app: AppState) {
    if !enabled() {
        return;
    }
    tokio::time::sleep(Duration::from_secs(20)).await;
    loop {
        check(&app).await;
        tokio::time::sleep(Duration::from_secs(24 * 3600)).await;
    }
}

fn status(u: &User) -> Value {
    let s = STATE.read().unwrap().clone();
    let releases = s["releases"].as_array().cloned().unwrap_or_default();
    let manage = require_owner(u).is_ok();
    json!({
        "enabled": enabled(),
        "current": CURRENT,
        "latest": releases.first().map(|r| r["version"].clone()).unwrap_or(json!(CURRENT)),
        "updateAvailable": !releases.is_empty(),
        "releases": releases,
        "checkedAt": s["checkedAt"],
        "error": if manage { s["error"].clone() } else { Value::Null },
        "canInstall": manage && env(&["FLOWWOLF_UPDATE_WEBHOOK"]).is_some(),
        "canCheck": manage,
        "howTo": {
            "docker": "docker compose pull && docker compose up -d",
            "binary": "Download the new release from https://github.com/WolfcastleDE/flowwolf/releases, replace the flowwolf binary and restart it.",
        },
    })
}

/// GET /updates — what the editor shows (everyone sees whether an update exists).
pub async fn get(Extension(u): Extension<User>) -> R {
    ok(status(&u))
}

/// POST /updates/check — check now (owners and admins).
pub async fn check_now(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    check(&app).await;
    ok(status(&u))
}

/// POST /updates/install — call the deploy hook (owners and admins).
pub async fn install(State(app): State<AppState>, Extension(u): Extension<User>) -> R {
    require_owner(&u)?;
    let hook = env(&["FLOWWOLF_UPDATE_WEBHOOK"])
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "No update webhook is configured (FLOWWOLF_UPDATE_WEBHOOK). Update with Docker or the binary instead."))?;
    let latest = STATE.read().unwrap()["releases"][0]["version"].clone();
    // Deploy hooks (Dokploy, …) expect a GitHub push for the app's branch; the extra fields say what and who.
    let branch = env(&["FLOWWOLF_UPDATE_BRANCH"]).unwrap_or_else(|| "main".into());
    let body = json!({"ref": format!("refs/heads/{branch}"), "repository": {"full_name": "WolfcastleDE/flowwolf"},
                      "source": "flowwolf", "from": CURRENT, "to": latest, "requestedBy": u.email});
    let mut rb = app.http.post(&hook).header("x-github-event", "push").json(&body);
    if let Some(t) = env(&["FLOWWOLF_UPDATE_WEBHOOK_TOKEN"]) {
        rb = rb.bearer_auth(t);
    }
    let r = rb.timeout(Duration::from_secs(30)).send().await
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("The update webhook could not be reached: {e}")))?;
    if !r.status().is_success() {
        return Err(err(StatusCode::BAD_GATEWAY, format!("The update webhook answered {}", r.status())));
    }
    tracing::info!("update requested by {} ({} -> {})", u.email, CURRENT, latest);
    ok(json!({"started": true, "message": "Update started. Flowwolf restarts in a few minutes; this page reconnects on its own."}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_and_releases() {
        assert_eq!(parse("v1.2.3"), Some((1, 2, 3, false)));
        assert_eq!(parse("0.2"), Some((0, 2, 0, false)));
        assert_eq!(parse("v1.0.0-rc.1"), Some((1, 0, 0, true)));
        assert_eq!(parse("latest"), None);
        let list = json!([
            {"tag_name": "v0.3.0", "name": "", "html_url": "u3", "body": "three", "draft": false, "prerelease": false},
            {"tag_name": "v0.10.0", "name": "Big", "html_url": "u10", "body": "ten", "draft": false, "prerelease": false},
            {"tag_name": "v0.4.0-rc.1", "draft": false, "prerelease": true},
            {"tag_name": "v0.5.0", "draft": true, "prerelease": false},
            {"tag_name": "v0.1.0", "draft": false, "prerelease": false},
            {"tag_name": "nightly", "draft": false, "prerelease": false},
        ]);
        let n = newer(&list, "0.1.0");
        assert_eq!(n.iter().map(|r| r["version"].as_str().unwrap()).collect::<Vec<_>>(), ["0.10.0", "0.3.0"]);
        assert_eq!(n[1]["name"], "v0.3.0");
        assert!(newer(&list, "0.10.0").is_empty());
    }
}
