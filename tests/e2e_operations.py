"""Every operation of every declarative integration, once.

All fixed API hosts are redirected to a catch-all HTTP server started by this script; credentials get
dummy values (URL-like fields point at the catch-all server). Each operation runs with sample values in
all of its fields and must end with success or a readable error, never a crash or a hang. It also
counts how many operations actually reached the server, i.e. built and sent their request.

Usage:
  docker run -d -p 5679:5678 -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_operations.py --overrides)" flowwolf:latest
  python tests/e2e_operations.py [base_url]
"""

import http.server
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, "..", "src", "nodes", "integrations.json"), encoding="utf-8"))
PORT = 5799
SINK = f"http://host.docker.internal:{PORT}"

URLISH = ("url", "uri", "baseurl", "server", "host", "domain", "instance", "endpoint", "apiurl", "siteurl", "storeurl")
FAKE_URL, FAKE = "https://fwx.test", "fwx"


def filled(base):
    """The base URL as it looks with the dummy credential values ({cred.x} or {cred.x|fallback})."""
    import re
    def sub(m):
        key = m.group(1).split("|")[0]
        return FAKE_URL if any(u in key.lower() for u in URLISH) else FAKE
    return re.sub(r"\{cred\.([^}]+)\}", sub, base).rstrip("/")


if "--overrides" in sys.argv:
    bases = {n["declarative"]["baseUrl"] for n in SPEC["nodes"] if n.get("declarative") and n["declarative"]["baseUrl"]}
    # plus hosts the hand-written parts of declarative nodes call directly (Drive uploads)
    bases |= {"https://www.googleapis.com/upload/drive/v3/files", FAKE_URL}
    concrete = sorted({filled(b) for b in bases if "{" not in filled(b)}, key=len, reverse=True)
    print(",".join(f"{b}={SINK}/{i}" for i, b in enumerate(concrete)))
    sys.exit()

import e2e_lib as L  # noqa: E402

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
L.login_or_setup()

hits = []


class Sink(http.server.BaseHTTPRequestHandler):
    """Answers every request with a JSON object that also carries the usual list roots."""

    def _answer(self):
        n = int(self.headers.get("content-length") or 0)
        if n:
            self.rfile.read(n)
        hits.append(self.path)
        body = json.dumps({"ok": True, "id": "1", "success": True, "data": [{"id": "1"}], "results": [], "items": [], "result": {"id": "1"}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Sink)
threading.Thread(target=server.serve_forever, daemon=True).start()

cred_types = {c["name"]: c for c in L.rest("GET", "/credential-types")}
cred_ids = {}


def credential(kind):
    if kind in cred_ids:
        return cred_ids[kind]
    data = {}
    for p in cred_types.get(kind, {}).get("properties", []):
        name = p["name"]
        if p.get("type") == "boolean":
            data[name] = False
        elif p.get("type") == "number":
            data[name] = 1
        elif p.get("type") == "options" and p.get("options"):
            data[name] = p.get("default") or p["options"][0]["value"]
        elif any(u in name.lower() for u in URLISH):
            data[name] = FAKE_URL
        else:
            data[name] = FAKE
    # A connected OAuth2 account (token without expiry) so OAuth2 nodes send their request.
    data["oauthTokenData"] = {"access_token": "tok"}
    cred_ids[kind] = L.rest("POST", "/credentials", {"name": f"ops {kind}", "type": kind, "data": data})["id"]
    return cred_ids[kind]


def sample(f):
    kind = f.get("kind", "string")
    return {"number": 1, "boolean": True, "json": "{}", "list": "a,b", "csv": "a,b", "dateTime": "2026-01-01T00:00:00Z"}.get(kind, "x1")


last_error = {}


def run(wf):
    last_error.clear()
    st, j = L.call("POST", "/rest/workflows/run", {"workflowData": wf})
    if st >= 400:
        return f"run refused {st} {str(j)[:100]}"
    eid, t0 = j["data"]["executionId"], time.time()
    while time.time() - t0 < 25:
        s2, e = L.call("GET", f"/rest/executions/{eid}")
        if s2 == 200 and e["data"]["status"] not in ("running", "new", "waiting"):
            ex = e["data"]
            if ex["status"] == "success":
                return None
            if ex["status"] != "error":
                return f"status {ex['status']}"
            msg = str((ex["data"]["resultData"].get("error") or {}).get("message", ""))
            last_error["msg"] = msg
            return None if msg and "panicked" not in msg and "RUST_BACKTRACE" not in msg else f"unreadable error {msg[:100]!r}"
        time.sleep(0.1)
    L.call("POST", f"/rest/executions/{eid}/stop")
    return "still running after 25 s"


# Nodes with their own client (not HTTP to a fixed API) are tested against real servers in e2e_storage.py.
OWN_CLIENTS = {"n8n-nodes-base.mongoDb", "n8n-nodes-base.awsS3", "n8n-nodes-base.ftp"}
bad, ops, reached, missed = [], 0, 0, []
for n in SPEC["nodes"]:
    d = n.get("declarative")
    if not d or n.get("group") == ["trigger"] or "trigger" in n.get("group", []):
        continue
    # every credential the node knows (e.g. Discord bot token and webhook)
    kinds = ([d["credential"]] if d.get("credential") else []) + [c["name"] for c in n.get("credentials", []) if not c["name"].startswith("$")]
    creds = {k: {"id": credential(k), "name": k} for k in dict.fromkeys(kinds)}
    for key, op in d["operations"].items():
        resource, operation = key.split(":", 1)
        params = {"resource": resource, "operation": operation, "returnAll": False, "limit": 2}
        for f in op.get("fields", []):
            params[f["name"]] = sample(f)
        if op.get("extra"):
            params[op.get("extraName") or "additionalFields"] = {f["name"]: sample(f) for f in op["extra"][:3]}
        wf = {"name": f"op {n['name']} {key}", "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
            {"name": "N", "type": n["name"], "typeVersion": n.get("version", 1), "position": [200, 0], "parameters": params, "credentials": creds}],
            "connections": {"Start": {"main": [[{"node": "N", "type": "main", "index": 0}]]}}}
        before = len(hits)
        ops += 1
        problem = run(wf)
        if problem and problem.startswith("still running"):
            problem = run(wf)  # one retry: the local catch-all server can stall under load
        if len(hits) > before:
            reached += 1
        elif n["name"] not in OWN_CLIENTS and "binary property" not in last_error.get("msg", ""):
            missed.append((n["name"], key, d.get("auth"), last_error.get("msg", "")))
        if problem:
            bad.append(f"{n['name']} {key}: {problem}")
if "--why" in sys.argv:
    from collections import Counter
    reasons = Counter((str(m[2]), m[3][:70]) for m in missed)
    for (auth, msg), c in reasons.most_common(25):
        print(f"  {c:4}  auth={auth}  {msg}")
print(f"  ran {ops} operations of {len(SPEC['nodes'])} generated nodes; {reached} sent their request to the catch-all server")
L.check("every operation runs or fails readably", not bad, "\n  " + "\n  ".join(bad[:40]) + (f"\n  … {len(bad) - 40} more" if len(bad) > 40 else ""))
L.check("every HTTP operation builds and sends its request", not missed, "\n  " + "\n  ".join(f"{m[0]} {m[1]}: {m[3][:90]}" for m in missed[:40])
        + (f"\n  … {len(missed) - 40} more" if len(missed) > 40 else ""))
L.check("server is still healthy", L.call("GET", "/healthz")[0] == 200)
print(f"\nall {L.passed} operation checks passed")
