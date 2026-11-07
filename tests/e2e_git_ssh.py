"""Source control over SSH against a real Gitea (its OpenSSH server): generated deploy keys,
host key pinning, push, pull, round trip between two instances and a rejected key.

Usage: python tests/e2e_git_ssh.py <base_a> <base_b> <ssh_repo_url> <gitea_api_from_host> <token>
  ssh_repo_url as seen from the containers, e.g. ssh://git@host.docker.internal:3322/flow/automations.git
"""

import json
import sys
import urllib.request

import e2e_lib as L

A, B, URL, GITEA, TOKEN = sys.argv[1:6]
node, chain, check = L.node, L.chain, L.check


def gitea(method, path, body=None):
    req = urllib.request.Request(GITEA + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"authorization": f"token {TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def on(base):
    L.BASE = base
    L.login_or_setup()
    return L.rest


def setup(base, title, register=True):
    rest = on(base)
    pub = rest("POST", "/source-control/generate-key-pair")["publicKey"]
    if register:
        gitea("POST", "/repos/flow/automations/keys", {"title": f"{title}-{pub.split()[1][-10:]}", "key": pub, "read_only": False})
    rest("PUT", "/source-control/preferences", {"provider": "ssh", "repositoryUrl": URL, "branch": "main", "folder": "over-ssh"})
    return rest, pub


rest, pub = setup(A, "flowwolf-a")
check("an ed25519 deploy key is generated, only the public half is shown", pub.startswith("ssh-ed25519 ") and "PRIVATE" not in json.dumps(rest("GET", "/source-control/preferences")))
check("branches are listed over SSH", "main" in rest("GET", "/source-control/branches"))
prefs = rest("GET", "/source-control/preferences")
check("the server host key is pinned on first contact", len(prefs.get("hostKey") or "") == 43, f"{prefs}")

wf = rest("POST", "/workflows", {"name": "Via SSH", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("NoOp", "n8n-nodes-base.noOp", 200)],
                                 "connections": chain("Start", "NoOp")})
r = rest("POST", "/source-control/push", {"message": "first push over ssh"})
check("push over SSH commits the workflow", r["workflows"] >= 1 and r["variables"] in (0, 1), f"{r}")
f = gitea("GET", f"/repos/flow/automations/contents/over-ssh/workflows/{wf['id']}.json?ref=main")
commit = gitea("GET", "/repos/flow/automations/commits?sha=main&limit=1")[0]["commit"]
check("the file is in the repository, committed by Flowwolf", f["type"] == "file" and commit["message"].strip() == "first push over ssh" and commit["author"]["name"] == "Flowwolf", f"{commit}")
r = rest("POST", "/source-control/push", {"message": "nothing new"})
check("a push without changes creates no commit", r == {"workflows": 0, "credentials": 0, "variables": 0}, f"{r}")

rest_b, _ = setup(B, "flowwolf-b")
r = rest_b("POST", "/source-control/pull")
mine = [w for w in rest_b("GET", "/workflows") if w["id"] == wf["id"]]
check("the second instance pulls the workflow over SSH", r["workflowsCreated"] >= 1 and mine and mine[0]["name"] == "Via SSH", f"{r}")
rest_b("PATCH", f"/workflows/{wf['id']}", {"name": "Edited on B"})
rest_b("POST", "/source-control/push", {"message": "edit from b"})
rest = on(A)
r = rest("POST", "/source-control/pull")
check("changes from B arrive on A (fetch of new commits)", rest("GET", f"/workflows/{wf['id']}")["name"] == "Edited on B" and r["workflowsUpdated"] >= 1, f"{r}")
rest("PATCH", f"/workflows/{wf['id']}", {"name": "Edited on A again"})
r = rest("POST", "/source-control/push", {"message": "edit from a"})
check("A pushes on top of B's commit (no force push needed)", r["workflows"] == 1, f"{r}")

on(B)
L.rest("POST", "/source-control/generate-key-pair")  # new key, never added to Gitea
st, body = L.call("POST", "/rest/source-control/push", {"message": "x"})
check("an unregistered key is rejected with a clear message", st == 400 and ("deploy key" in json.dumps(body).lower() or "auth" in json.dumps(body).lower()), f"{st} {body}")

print(f"\nall {L.passed} git-over-ssh checks passed")
