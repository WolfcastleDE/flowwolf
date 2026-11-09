"""Parked executions survive a restart.

Usage: python tests/e2e_persist.py [base_url] [container_name]   (fresh instance, takes ~80 s)
"""

import subprocess
import sys
import time
import urllib.request

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
CONTAINER = sys.argv[2] if len(sys.argv) > 2 else "fw-e2e"
call, rest, node, chain, out, check = L.call, L.rest, L.node, L.chain, L.out, L.check
L.login_or_setup()

hook = rest("POST", "/workflows", {"name": "persist", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "persist"}, 2, webhookId="w-persist"),
    node("SetA", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "a", "type": "string", "value": "={{ $json.query.v }}"}]}}, 3.4),
    node("Wait", "n8n-nodes-base.wait", 400, {"resume": "webhook"}, 1.1),
    node("After", "n8n-nodes-base.set", 600, {"mode": "manual", "assignments": {"assignments": [{"name": "b", "type": "string", "value": "={{ $('SetA').item.json.a }}-{{ $json.body.x }}"}]}}, 3.4),
    node("Other", "n8n-nodes-base.noOp", 400)],
    "connections": {"Hook": {"main": [[{"node": "SetA", "type": "main", "index": 0}]]},
                    "SetA": {"main": [[{"node": "Wait", "type": "main", "index": 0}, {"node": "Other", "type": "main", "index": 0}]]},
                    "Wait": {"main": [[{"node": "After", "type": "main", "index": 0}]]}}})
timed = rest("POST", "/workflows", {"name": "persist time", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "persist-time"}, 2, webhookId="w-time"),
    node("Wait", "n8n-nodes-base.wait", 200, {"resume": "specificTime", "dateTime": "={{ $now.plus({seconds: 66}).toISO() }}"}, 1.1),
    node("Done", "n8n-nodes-base.set", 400, {"mode": "manual", "assignments": {"assignments": [{"name": "done", "type": "boolean", "value": True}]}}, 3.4)],
    "connections": chain("Hook", "Wait", "Done")})
for w in (hook, timed):
    rest("POST", f"/workflows/{w['id']}/activate")

call("GET", "/webhook/persist?v=7")
call("GET", "/webhook/persist-time")
time.sleep(1.0)
e1 = rest("GET", f"/executions?workflowId={hook['id']}")[0]
e2 = rest("GET", f"/executions?workflowId={timed['id']}")[0]
check("webhook wait is parked", e1["status"] == "waiting")
check("long time wait is parked instead of holding a thread", e2["status"] == "waiting")

subprocess.run(["docker", "restart", CONTAINER], check=True, capture_output=True)
for _ in range(60):
    try:
        urllib.request.urlopen(L.BASE + "/healthz", timeout=2)
        break
    except Exception:
        time.sleep(0.5)
check("still waiting after a restart", rest("GET", f"/executions/{e1['id']}")["status"] == "waiting")

st, body = call("POST", f"/webhook-waiting/{e1['id']}", {"x": "y"})
check("resume URL works after the restart", st == 200, f"{st} {body}")
ex = L.wait_exec(e1["id"])
rd = ex["data"]["resultData"]["runData"]
check("execution continues with data from before the restart", ex["status"] == "success" and out(ex, "After") == [{"b": "7-y"}], f"{ex['status']} {ex['data']['resultData'].get('error')}")
check("the pending sibling branch also ran", "Other" in rd and len(rd["Wait"]) == 1)
check("the same execution id is continued", len(rest("GET", f"/executions?workflowId={hook['id']}")) == 1)

for _ in range(120):
    if rest("GET", f"/executions/{e2['id']}")["status"] != "waiting":
        break
    time.sleep(1)
ex = L.wait_exec(e2["id"])
check("timed wait resumes after the restart", ex["status"] == "success" and out(ex, "Done") == [{"done": True}], f"{ex['status']}")


# ---------------------------------------------------------------- restarts while something runs
def wait_up():
    for _ in range(120):
        try:
            urllib.request.urlopen(L.BASE + "/healthz", timeout=2)
            return
        except Exception:
            time.sleep(0.5)


def busy(secs):
    """A manual run that keeps a Code node busy for `secs` seconds."""
    wf = {"name": f"busy {secs}", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
          node("Busy", "n8n-nodes-base.code", 200, {"jsCode": f"const t = Date.now(); while (Date.now() - t < {secs * 1000}) {{}} return [{{json: {{done: true}}}}];"}, 2)],
          "connections": chain("Start", "Busy")}
    eid = rest("POST", "/workflows/run", {"workflowData": wf})["executionId"]
    time.sleep(1.0)
    return eid


eid = busy(4)
subprocess.run(["docker", "restart", "-t", "30", CONTAINER], check=True, capture_output=True)
wait_up()
ex = rest("GET", f"/executions/{eid}")
check("a normal restart (update) lets a running execution finish first", ex["status"] == "success", ex["status"])

eid = busy(30)
subprocess.run(["docker", "kill", CONTAINER], check=True, capture_output=True)
subprocess.run(["docker", "start", CONTAINER], check=True, capture_output=True)
wait_up()
ex = rest("GET", f"/executions/{eid}")
msg = str((ex.get("data") or {}).get("resultData", {}).get("error", {}).get("message", ""))
check("a run cut off by a hard stop is marked with the reason", ex["status"] == "crashed" and "restarted while this execution was running" in msg, f"{ex['status']} {msg}")

print(f"\nall {L.passed} persistence checks passed")
