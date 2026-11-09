"""Update notices and the update button.

The releases list and the deploy hook are webhook workflows of the same instance, so start it with:
  docker run -d -p 5679:5678 -e FLOWWOLF_UPDATE_URL=http://127.0.0.1:5678/webhook/releases \\
    -e FLOWWOLF_UPDATE_WEBHOOK=http://127.0.0.1:5678/webhook/deployhook flowwolf:latest
Usage: python tests/e2e_updates.py [base_url]
"""

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
rest, node, chain, check = L.rest, L.node, L.chain, L.check
L.login_or_setup()

# Versions relative to the running one, so the test keeps working after every release.
CURRENT = rest("GET", "/updates")["current"]
MAJ, MIN, PAT = (int(x) for x in CURRENT.split(".")[:3])
NEW, PATCH = f"{MAJ}.{MIN + 1}.0", f"{MAJ}.{MIN}.{PAT + 5}"
RELEASES = [
    {"tag_name": f"v{MAJ}.{MIN + 2}.0-rc.1", "name": "rc", "html_url": "https://example.test/rc", "body": "pre", "draft": False, "prerelease": True},
    {"tag_name": f"v{NEW}", "name": f"Flowwolf {NEW}", "html_url": f"https://example.test/{NEW}", "published_at": "2026-12-01T10:00:00Z",
     "body": "### Changed\n- Faster webhooks\n- `$now` uses the time zone", "draft": False, "prerelease": False},
    {"tag_name": f"v{MAJ}.{MIN + 9}.0", "name": "draft", "draft": True, "prerelease": False},
    {"tag_name": f"v{PATCH}", "name": "", "html_url": f"https://example.test/{PATCH}", "body": "fixes", "draft": False, "prerelease": False},
    {"tag_name": f"v{CURRENT}", "name": f"Flowwolf {CURRENT}", "draft": False, "prerelease": False},
]


def mock(name, method, path, body, code=200):
    wf = rest("POST", "/workflows", {"name": name, "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": path, "responseMode": "responseNode"}, 2),
        node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": json.dumps(body), "options": {"responseCode": code}}, 1.1)],
        "connections": chain("Hook", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf["id"]


before = rest("GET", "/updates")
check("status without a release list: no update, version shown", before["updateAvailable"] is False and before["current"] == before["latest"]
      and before["enabled"] is True, str(before))

mock("releases", "GET", "releases", RELEASES)
u = rest("POST", "/updates/check")
check("newer releases are found, newest first; drafts and pre-releases are left out",
      u["updateAvailable"] is True and u["latest"] == NEW and [r["version"] for r in u["releases"]] == [NEW, PATCH], str(u))
check("release notes, links and names come along", u["releases"][0]["notes"].startswith("### Changed") and u["releases"][0]["url"] == f"https://example.test/{NEW}"
      and u["releases"][1]["name"] == f"v{PATCH}")
check("owners can check and install (a deploy hook is set)", u["canCheck"] is True and u["canInstall"] is True and u["checkedAt"])

# a member sees the notice but cannot check or install
inv = rest("POST", "/invitations", [{"email": "mia@example.com", "role": "global:member"}])[0]["user"]
token = urllib.parse.parse_qs(urllib.parse.urlparse(inv["inviteAcceptUrl"]).fragment.split("?", 1)[1])["token"][0]
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def member(method, path, body=None):
    req = urllib.request.Request(L.BASE + "/rest" + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with op.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


member("POST", "/invitations/accept", {"token": token, "password": "mia password 1"})
st, mu = member("GET", "/updates")
check("members see that an update exists", st == 200 and mu["data"]["updateAvailable"] is True and mu["data"]["canInstall"] is False and mu["data"]["canCheck"] is False)
check("members can neither check nor install", member("POST", "/updates/check")[0] == 403 and member("POST", "/updates/install")[0] == 403)

# the button calls the deploy hook with the target version
hook = mock("deploy hook", "POST", "deployhook", {"ok": True})
r = rest("POST", "/updates/install")
time.sleep(1)
ex = rest("GET", f"/executions?workflowId={hook}")
runs = ex if isinstance(ex, list) else ex.get("results", ex.get("data", []))
detail = rest("GET", f"/executions/{runs[0]['id']}") if runs else {}
body = detail.get("data", {}).get("resultData", {}).get("runData", {}).get("Hook", [{}])[0].get("data", {}).get("main", [[{}]])[0][0].get("json", {}).get("body", {}) if detail else {}
headers = detail.get("data", {}).get("resultData", {}).get("runData", {}).get("Hook", [{}])[0].get("data", {}).get("main", [[{}]])[0][0].get("json", {}).get("headers", {}) if detail else {}
check("install calls the deploy hook with from/to versions", r["started"] is True and len(runs) == 1 and body.get("to") == NEW
      and body.get("from") == before["current"] and body.get("requestedBy") == "owner@example.com", f"{r} {body}")
check("the hook gets a GitHub push for the branch (what Dokploy expects)", headers.get("x-github-event") == "push" and body.get("ref") == "refs/heads/main",
      f"{headers.get('x-github-event')} {body.get('ref')}")

# a failing hook is reported, not swallowed
rest("POST", f"/workflows/{hook}/deactivate")
mock("broken hook", "POST", "deployhook", {"ok": False}, code=500)
st, j = L.call("POST", "/rest/updates/install")
check("a failing deploy hook is reported", st == 502 and "500" in j["message"], f"{st} {j}")

print(f"\nall {L.passed} update checks passed")
