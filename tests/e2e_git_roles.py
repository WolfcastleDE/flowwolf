"""Git source control against a real Gitea, plus viewer/editor share roles.

Usage: python tests/e2e_git_roles.py <base_a> <base_b> <gitea_api_from_containers> <gitea_api_from_host> <token>
Both Flowwolf instances must be fresh.
"""

import base64
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

import e2e_lib as L

BASE_A, BASE_B, GITEA_INNER, GITEA_HOST, TOKEN = sys.argv[1:6]
L.BASE = BASE_A
rest, node, chain, check = L.rest, L.node, L.chain, L.check
L.login_or_setup()


class Session:
    """Separate cookie jar per user/instance."""

    def __init__(self, base):
        self.base, self.op = base, urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + "/rest" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"content-type": "application/json"})
        try:
            with self.op.open(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


def gitea_file(path):
    req = urllib.request.Request(f"{GITEA_HOST}/repos/flow/automations/raw/{path}?ref=main", headers={"authorization": f"token {TOKEN}"})
    try:
        return urllib.request.urlopen(req).read().decode()
    except urllib.error.HTTPError:
        return None


def commits():
    req = urllib.request.Request(f"{GITEA_HOST}/repos/flow/automations/commits?limit=50&stat=false", headers={"authorization": f"token {TOKEN}"})
    return len(json.loads(urllib.request.urlopen(req).read()))


PREFS = {"provider": "gitea", "apiUrl": GITEA_INNER, "repository": "flow/automations", "branch": "main", "folder": "", "token": TOKEN}

# ---------------------------------------------------------------- instance A: content + push
wf = rest("POST", "/workflows", {"name": "Synced flow", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Set", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "v", "type": "number", "value": 1}]}}, 3.4)], "connections": chain("Start", "Set")})
cred = rest("POST", "/credentials", {"name": "Prod API", "type": "httpHeaderAuth", "data": {"name": "X-Key", "value": "s3cret-value"}})
rest("POST", "/variables", {"key": "REGION", "value": "eu-central"})
rest("PUT", "/source-control/preferences", PREFS)
check("token is stored but never returned", rest("GET", "/source-control/preferences")["tokenSet"] is True and "token" not in rest("GET", "/source-control/preferences"))
check("branches of the repository are listed", "main" in rest("GET", "/source-control/branches"))
r = rest("POST", "/source-control/push", {"message": "initial"})
check("push writes workflow, credential stub and variables", r == {"workflows": 1, "credentials": 1, "variables": 1}, f"{r}")
f = gitea_file(f"workflows/{wf['id']}.json")
check("workflow file is n8n JSON in the repository", f is not None and json.loads(f)["name"] == "Synced flow" and json.loads(f)["nodes"][1]["type"] == "n8n-nodes-base.set")
stub = gitea_file(f"credential_stubs/{cred['id']}.json")
check("credential stub contains no secret", stub is not None and "s3cret" not in stub and json.loads(stub)["data"] == {})
check("variable values are not pushed", "eu-central" not in gitea_file("variable_stubs.json") and "REGION" in gitea_file("variable_stubs.json"))
n = commits()
r = rest("POST", "/source-control/push", {"message": "again"})
check("push without changes creates no commits", r == {"workflows": 0, "credentials": 0, "variables": 0} and commits() == n, f"{r}")

# ---------------------------------------------------------------- instance B: pull
b = Session(BASE_B)
b.call("POST", "/owner/setup", {"email": "b@example.com", "password": "second instance 42", "firstName": "Bea"})
b.call("PUT", "/source-control/preferences", PREFS)
st, r = b.call("POST", "/source-control/pull")
check("pull creates the workflow, credential stub and variable", st == 200 and r["data"] == {"workflowsCreated": 1, "workflowsUpdated": 0, "credentialStubs": 1, "variables": 1}, f"{r}")
st, w = b.call("GET", f"/workflows/{wf['id']}")
check("pulled workflow keeps id, name and nodes", st == 200 and w["data"]["name"] == "Synced flow" and len(w["data"]["nodes"]) == 2)
st, c = b.call("GET", f"/credentials/{cred['id']}")
check("pulled credential is an empty stub to fill in", st == 200 and c["data"]["name"] == "Prod API" and not c["data"]["data"].get("name"))

w_a = rest("GET", f"/workflows/{wf['id']}")
w_a["nodes"][1]["parameters"]["assignments"]["assignments"][0]["value"] = 2
w_a["name"] = "Synced flow v2"
rest("PATCH", f"/workflows/{wf['id']}", {"name": w_a["name"], "nodes": w_a["nodes"]})
rest("POST", "/source-control/push", {"message": "v2"})
st, r = b.call("POST", "/source-control/pull")
st, w = b.call("GET", f"/workflows/{wf['id']}")
check("changes arrive with the next pull", r["data"]["workflowsUpdated"] == 1 and w["data"]["name"] == "Synced flow v2"
      and w["data"]["nodes"][1]["parameters"]["assignments"]["assignments"][0]["value"] == 2, f"{r}")

# ---------------------------------------------------------------- roles (instance A)
rest("POST", "/users", {"email": "viewer@example.com", "password": "viewer pass 42", "role": "member"})
uid = [u for u in rest("GET", "/users") if u["email"] == "viewer@example.com"][0]["id"]
m = Session(BASE_A)
m.call("POST", "/login", {"email": "viewer@example.com", "password": "viewer pass 42"})
rest("PUT", f"/workflows/{wf['id']}/share", {"shares": [{"userId": uid, "role": "viewer"}]})
check("share role is stored", rest("GET", f"/workflows/{wf['id']}/share")["shares"] == [{"userId": uid, "role": "viewer"}])
st, w = m.call("GET", f"/workflows/{wf['id']}")
check("viewer can open the workflow, read-only", st == 200 and w["data"]["canEdit"] is False)
st, _ = m.call("PATCH", f"/workflows/{wf['id']}", {"name": "hacked"})
check("viewer cannot save", st == 403)
st, _ = m.call("POST", "/workflows/run", {"workflowData": w["data"]})
check("viewer cannot run", st == 403)
rest("PUT", f"/workflows/{wf['id']}/share", {"shares": [{"userId": uid, "role": "editor"}]})
st, _ = m.call("PATCH", f"/workflows/{wf['id']}", {"name": "edited by editor"})
check("editor can save", st == 200 and rest("GET", f"/workflows/{wf['id']}")["name"] == "edited by editor")
st, _ = m.call("DELETE", f"/workflows/{wf['id']}")
check("editor still cannot delete", st == 403)
st, _ = m.call("POST", "/source-control/push", {})
check("members cannot use source control", st == 403)

print(f"\nall {L.passed} git & role checks passed")
