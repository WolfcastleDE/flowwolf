"""Queue mode with separate worker containers, plus workflow folders.

Usage: python tests/e2e_queue_workers.py [base_url]
Expects a main container with EXECUTIONS_MODE=queue and two `flowwolf worker` containers
(hostnames w1, w2, FLOWWOLF_WORKER_CONCURRENCY=3) on the same data volume.
"""

import sys
import threading
import time

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
call, rest, node, chain, out, check = L.call, L.rest, L.node, L.chain, L.out, L.check
L.login_or_setup()


def activate(wf):
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf


# ---------------------------------------------------------------- results computed by workers
calc = activate(rest("POST", "/workflows", {"name": "calc", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "calc", "responseMode": "lastNode"}, 2, webhookId="w-calc"),
    node("Pause", "n8n-nodes-base.wait", 200, {"resume": "timeInterval", "amount": 1, "unit": "seconds"}, 1.1),
    node("Square", "n8n-nodes-base.code", 400, {"jsCode": "return [{json: {n: Number($('Hook').first().json.query.n) ** 2}}]"}, 2)],
    "connections": chain("Hook", "Pause", "Square")}))
results = {}


def hit(n):
    results[n] = call("GET", f"/webhook/calc?n={n}")


threads = [threading.Thread(target=hit, args=(n,)) for n in range(1, 7)]
t0 = time.time()
[t.start() for t in threads]
[t.join() for t in threads]
check("lastNode responses are computed on workers", all(results[n] == (200, {"n": n * n}) for n in range(1, 7)), f"{results}")
check("six 1-second runs finish in parallel on the workers", time.time() - t0 < 4.5, f"{time.time() - t0:.1f}s")
execs = rest("GET", f"/executions?workflowId={calc['id']}")
workers = {e["worker"] for e in execs}
check("executions ran on worker processes", None not in workers and workers <= {"w1", "w2"}, f"{workers}")
check("both workers took work", workers == {"w1", "w2"}, f"{workers}")

reply = activate(rest("POST", "/workflows", {"name": "reply", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "reply", "responseMode": "responseNode"}, 2, webhookId="w-reply"),
    node("Respond", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": "={{ JSON.stringify({echo: $json.body.msg}) }}", "options": {"responseCode": 202}}, 1.1)],
    "connections": chain("Hook", "Respond")}))
st, body = call("POST", "/webhook/reply", {"msg": "via worker"})
check("Respond to Webhook works across processes", st == 202 and body == {"echo": "via worker"}, f"{st} {body}")

ex = L.run_manual({"name": "manual", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0)], "connections": {}})
check("manual test runs stay in the main process", ex["status"] == "success" and rest("GET", "/executions?limit=1")[0]["worker"] is None)

# ---------------------------------------------------------------- stop a run on a worker
slow = activate(rest("POST", "/workflows", {"name": "slow", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "slow"}, 2, webhookId="w-slow"),
    node("Pause", "n8n-nodes-base.wait", 200, {"resume": "timeInterval", "amount": 30, "unit": "seconds"}, 1.1)], "connections": chain("Hook", "Pause")}))
call("GET", "/webhook/slow")
time.sleep(1.5)
eid = rest("GET", f"/executions?workflowId={slow['id']}")[0]["id"]
rest("POST", f"/executions/{eid}/stop")
t0 = time.time()
ex = L.wait_exec(eid)
check("stop reaches the worker", ex["status"] == "canceled" and time.time() - t0 < 5, f"{ex['status']} {time.time() - t0:.1f}s")

# ---------------------------------------------------------------- folders
w = rest("POST", "/workflows", {"name": "Lead intake", "folder": "Sales/Leads", "nodes": [], "connections": {}})
listing = {x["id"]: x for x in rest("GET", "/workflows")}
check("folder is stored and listed", listing[w["id"]]["folder"] == "Sales/Leads")
rest("PATCH", f"/workflows/{w['id']}", {"folder": "Archive"})
check("workflow can be moved to another folder", rest("GET", f"/workflows/{w['id']}")["folder"] == "Archive")

print(f"\nall {L.passed} queue/worker/folder checks passed")
