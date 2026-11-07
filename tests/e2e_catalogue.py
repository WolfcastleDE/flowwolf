"""Every node type of the catalogue, once:

1. its description is well formed: properties have a name and type, defaults of option lists are one of the
   options, displayOptions only refer to parameters the node has, credentials exist as credential types;
2. it runs with its default parameters (no credentials) and ends with success or a readable error —
   never a crash, a server error or a hang.

Usage: python tests/e2e_catalogue.py [base_url]   (fresh instance)
"""

import json
import sys
import time

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
L.login_or_setup()
nodes = L.rest("GET", "/node-types")
cred_types = {c["name"] for c in L.rest("GET", "/credential-types")}
L.check("catalogue loads", len(nodes) >= 400, str(len(nodes)))

# ---------------------------------------------------------------- 1. descriptions
problems = []
for n in nodes:
    props = n.get("properties", [])
    names = {p.get("name") for p in props}
    if not n.get("displayName") or not n.get("name"):
        problems.append(f"{n.get('name')}: missing name/displayName")
    for c in n.get("credentials", []):
        if not c["name"].startswith("$") and c["name"] not in cred_types:
            problems.append(f"{n['name']}: unknown credential type {c['name']}")
    for p in props:
        where = f"{n['name']}.{p.get('name')}"
        if not p.get("name") or not p.get("type"):
            problems.append(f"{where}: property without name or type")
            continue
        if p["type"] == "options" and p.get("options"):
            values = [o.get("value") for o in p["options"]]
            if "default" in p and p["default"] not in values and p["default"] not in ("", None) and not str(p["default"]).startswith("="):
                problems.append(f"{where}: default {p['default']!r} is not an option")
        for cond in ("show", "hide"):
            for ref in (p.get("displayOptions", {}).get(cond) or {}):
                if ref.startswith("@") or ref.startswith("/"):
                    continue
                if ref not in names:
                    problems.append(f"{where}: displayOptions.{cond} refers to unknown parameter {ref!r}")
L.check("all node descriptions are well formed", not problems, "\n  " + "\n  ".join(problems[:40]) + (f"\n  … {len(problems) - 40} more" if len(problems) > 40 else ""))

# ---------------------------------------------------------------- 2. one run each
# Nodes that wait for the outside world by design (triggers, waits, human input) are not run here;
# their own suites cover them.
SKIP_SUBSTR = ("Trigger", "trigger", ".wait", ".form", "respondToWebhook", "chatTrigger", "sendAndWait", ".errorTrigger", ".start")
crashed, slow, ran = [], [], 0
for n in nodes:
    kind = n["name"]
    group = n.get("group", [])
    if "trigger" in group or any(s in kind for s in SKIP_SUBSTR) or n.get("subNodeType"):
        continue
    wf = {"name": f"cat {kind}", "nodes": [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
        {"name": "N", "type": kind, "typeVersion": n.get("version", 1) if not isinstance(n.get("version"), list) else n["version"][-1], "position": [200, 0], "parameters": {}}],
        "connections": {"Start": {"main": [[{"node": "N", "type": "main", "index": 0}]]}}}
    t0 = time.time()
    st, j = L.call("POST", "/rest/workflows/run", {"workflowData": wf})
    if st >= 500 or st == 0:
        crashed.append(f"{kind}: run request answered {st} {str(j)[:120]}")
        continue
    if st >= 400:
        crashed.append(f"{kind}: run refused {st} {str(j)[:120]}")
        continue
    eid = j["data"]["executionId"]
    ex = None
    while time.time() - t0 < 30:
        s2, e = L.call("GET", f"/rest/executions/{eid}")
        if s2 == 200 and e["data"]["status"] not in ("running", "new", "waiting"):
            ex = e["data"]
            break
        time.sleep(0.2)
    ran += 1
    if ex is None:
        slow.append(f"{kind}: still running after 30 s")
        L.call("POST", f"/rest/executions/{eid}/stop")
        continue
    if ex["status"] not in ("success", "error"):
        crashed.append(f"{kind}: ended with status {ex['status']}")
        continue
    if ex["status"] == "error":
        err = ex["data"]["resultData"].get("error") or {}
        msg = str(err.get("message", ""))
        if not msg or "panicked" in msg or "RUST_BACKTRACE" in msg or "internal error" in msg.lower():
            crashed.append(f"{kind}: unreadable error {msg[:120]!r}")
print(f"  ran {ran} node types")
L.check("every node runs with defaults or fails with a readable error", not crashed, "\n  " + "\n  ".join(crashed[:40]))
L.check("no node hangs without input", not slow, "\n  " + "\n  ".join(slow))
L.check("server is still healthy afterwards", L.call("GET", "/healthz")[0] == 200)

print(f"\nall {L.passed} catalogue checks passed")
