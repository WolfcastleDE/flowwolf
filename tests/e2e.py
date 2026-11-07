"""End-to-end test against a running Flowwolf instance (fresh data volume).

Usage: python tests/e2e.py [base_url]
"""

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5678"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json", **(headers or {})})
    try:
        with opener.open(req, timeout=60) as r:
            text = r.read().decode()
            return r.status, (text if raw else (json.loads(text) if text else None))
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def rest(method, path, body=None):
    status, j = call(method, "/rest" + path, body)
    assert status < 300, f"{method} {path} -> {status}: {j}"
    return j["data"]


def node(name, kind, x, params=None, version=1, **extra):
    return {"name": name, "type": kind, "typeVersion": version, "position": [x, 0], "parameters": params or {}, **extra}


def chain(*names):
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(names, names[1:])}


def wait_exec(exec_id):
    for _ in range(200):
        ex = rest("GET", f"/executions/{exec_id}")
        if ex["status"] != "running":
            return ex
        time.sleep(0.1)
    raise AssertionError("execution did not finish")


def run_manual(wf, dest=None):
    r = rest("POST", "/workflows/run", {"workflowData": wf, "destinationNode": dest})
    return wait_exec(r["executionId"])


def out(ex, name, branch=0):
    return [i["json"] for i in ex["data"]["resultData"]["runData"][name][-1]["data"]["main"][branch]]


passed = 0


def check(label, cond, detail=""):
    global passed
    assert cond, f"FAILED: {label} {detail}"
    passed += 1
    print(f"  ok  {label}")


# ---------------------------------------------------------------- setup & auth
s = rest("GET", "/settings")
check("fresh instance needs setup", s["setupComplete"] is False)
st, _ = call("GET", "/rest/workflows")
check("API rejects anonymous calls", st == 401)
st, _ = call("POST", "/rest/owner/setup", {"email": "owner@example.com", "password": "short"})
check("weak password rejected", st == 400)
me = rest("POST", "/owner/setup", {"email": "owner@example.com", "password": "correct horse 42", "firstName": "Ada", "lastName": "Owner"})
check("owner created and logged in", me["role"] == "owner" and rest("GET", "/login")["email"] == "owner@example.com")

# ---------------------------------------------------------------- webhook flow
code = "return $input.all().map(i => ({ json: { name: i.json.body.name, score: i.json.body.items.reduce((a, b) => a + b, 0) } }));"
wf = rest("POST", "/workflows", {
    "name": "E2E webhook",
    "nodes": [
        node("Webhook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "e2e/:user", "responseMode": "responseNode"}, 2, webhookId="hook-e2e"),
        node("Code", "n8n-nodes-base.code", 200, {"jsCode": code}, 2),
        node("If", "n8n-nodes-base.if", 400, {"conditions": {"combinator": "and", "options": {"caseSensitive": True}, "conditions": [
            {"leftValue": "={{ $json.score }}", "rightValue": 10, "operator": {"type": "number", "operation": "gt"}}]}}, 2),
        node("High", "n8n-nodes-base.set", 600, {"mode": "manual", "includeOtherFields": True, "include": "all", "assignments": {"assignments": [
            {"name": "level", "type": "string", "value": "=high for {{ $json.name.toUpperCase() }} ({{ $('Webhook').item.json.params.user }})"}]}}, 3.4),
        node("Low", "n8n-nodes-base.set", 600, {"mode": "manual", "assignments": {"assignments": [{"name": "level", "type": "string", "value": "low"}]}}, 3.4),
        node("Respond", "n8n-nodes-base.respondToWebhook", 800, {"respondWith": "firstIncomingItem", "options": {"responseCode": 201}}, 1.1),
    ],
    "connections": {**chain("Webhook", "Code", "If"),
                    "If": {"main": [[{"node": "High", "type": "main", "index": 0}], [{"node": "Low", "type": "main", "index": 0}]]},
                    "High": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
                    "Low": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
})
rest("POST", f"/workflows/{wf['id']}/activate")
st, body = call("POST", "/webhook/hook-e2e/e2e/u42", {"name": "ada", "items": [5, 6, 7]})
check("production webhook with Code/IF/Set/Respond", st == 201 and body == {"name": "ada", "score": 18, "level": "high for ADA (u42)"}, f"{st} {body}")
st, body = call("POST", "/webhook/hook-e2e/e2e/u1", {"name": "bob", "items": [1]})
check("false branch", body == {"level": "low"}, f"{body}")
st, _ = call("POST", "/webhook/nope")
check("unknown webhook is 404", st == 404)
execs = rest("GET", f"/executions?workflowId={wf['id']}")
check("executions are recorded", len(execs) == 2 and all(e["status"] == "success" for e in execs))

# ---------------------------------------------------------------- loop, merge, aggregate (manual run)
loop_wf = {
    "name": "Loop",
    "nodes": [
        node("Start", "n8n-nodes-base.manualTrigger", 0),
        node("Make", "n8n-nodes-base.code", 200, {"jsCode": "return Array.from({length: 5}, (_, i) => ({json: {id: i + 1}}));"}, 2),
        node("Loop", "n8n-nodes-base.splitInBatches", 400, {"batchSize": 2}, 3),
        node("Double", "n8n-nodes-base.set", 600, {"mode": "manual", "includeOtherFields": True, "assignments": {"assignments": [
            {"name": "double", "type": "number", "value": "={{ $json.id * 2 }}"}]}}, 3.4),
        node("Collect", "n8n-nodes-base.aggregate", 600, {"aggregate": "aggregateAllItemData", "destinationFieldName": "all"}),
    ],
    "connections": {**chain("Start", "Make", "Loop"),
                    "Loop": {"main": [[{"node": "Collect", "type": "main", "index": 0}], [{"node": "Double", "type": "main", "index": 0}]]},
                    "Double": {"main": [[{"node": "Loop", "type": "main", "index": 0}]]}},
}
ex = run_manual(loop_wf)
allv = out(ex, "Collect")[0]["all"]
check("Loop Over Items processes every batch", ex["status"] == "success" and [x["double"] for x in allv] == [2, 4, 6, 8, 10], f"{allv}")
check("loop ran 3 batches", len(ex["data"]["resultData"]["runData"]["Double"]) == 3)

merge_wf = {
    "name": "Merge",
    "nodes": [
        node("Start", "n8n-nodes-base.manualTrigger", 0),
        node("Users", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{id:1,name:'Ada'}},{json:{id:2,name:'Bob'}},{json:{id:3,name:'Cy'}}]"}, 2),
        node("Orders", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{id:1,total:30}},{json:{id:3,total:12}}]"}, 2),
        node("Merge", "n8n-nodes-base.merge", 400, {"mode": "combine", "combineBy": "combineByFields", "fieldsToMatchString": "id", "joinMode": "enrichInput1"}, 3),
        node("Sort", "n8n-nodes-base.sort", 600, {"sortFieldsUi": {"sortField": [{"fieldName": "name", "order": "descending"}]}}),
        node("Sum", "n8n-nodes-base.summarize", 800, {"fieldsToSummarize": {"values": [{"aggregation": "sum", "field": "total"}, {"aggregation": "count", "field": "id"}]}}),
    ],
    "connections": {"Start": {"main": [[{"node": "Users", "type": "main", "index": 0}, {"node": "Orders", "type": "main", "index": 0}]]},
                    "Users": {"main": [[{"node": "Merge", "type": "main", "index": 0}]]},
                    "Orders": {"main": [[{"node": "Merge", "type": "main", "index": 1}]]},
                    **chain("Merge", "Sort", "Sum")},
}
ex = run_manual(merge_wf)
check("Merge waits for both inputs and enriches input 1", [x["name"] for x in out(ex, "Sort")] == ["Cy", "Bob", "Ada"] and out(ex, "Sort")[0]["total"] == 12)
check("Summarize", out(ex, "Sum") == [{"sum_total": 42, "count_id": 3}], f"{out(ex, 'Sum')}")
ex = run_manual(merge_wf, dest="Merge")
check("test step stops at destination node", "Sort" not in ex["data"]["resultData"]["runData"])

pinned = dict(merge_wf, pinData={"Users": [{"json": {"id": 3, "name": "Pinned"}}]})
ex = run_manual(pinned)
check("pinned data replaces node output in manual runs", out(ex, "Sort")[0]["name"] == "Pinned")

# ---------------------------------------------------------------- HTTP Request, data nodes, expressions
http_wf = {
    "name": "HTTP",
    "nodes": [
        node("Start", "n8n-nodes-base.manualTrigger", 0),
        node("Get", "n8n-nodes-base.httpRequest", 200, {"method": "GET", "url": "=http://127.0.0.1:5678/{{ 'health' + 'z' }}",
                                                       "options": {"response": {"response": {"fullResponse": True}}}}, 4.2),
        node("Date", "n8n-nodes-base.dateTime", 400, {"operation": "formatDate", "date": "2024-03-05T10:20:00Z", "format": "custom", "customFormat": "dd.MM.yyyy", "options": {"timezone": "UTC"}}, 2),
        node("Hash", "n8n-nodes-base.crypto", 600, {"action": "hash", "type": "SHA256", "value": "={{ $json.formattedDate }}", "dataPropertyName": "h"}),
        node("Html", "n8n-nodes-base.set", 800, {"mode": "raw", "jsonOutput": "={{ JSON.stringify({ status: $('Get').item.json.statusCode, d: $json.formattedDate, n: $now.year > 2000, v: $vars.GREETING }) }}"}, 3.4),
    ],
    "connections": chain("Start", "Get", "Date", "Hash", "Html"),
}
rest("POST", "/variables", {"key": "GREETING", "value": "hi"})
ex = run_manual(http_wf)
res = out(ex, "Html")[0]
check("HTTP Request + Date & Time + Crypto + $vars", ex["status"] == "success" and res == {"status": 200, "d": "05.03.2024", "n": True, "v": "hi"}, f"{ex['status']} {res} {ex['data']['resultData'].get('error')}")
check("hash computed", len(out(ex, "Hash")[0]["h"]) == 64)

# ---------------------------------------------------------------- errors & error workflow
err_target = rest("POST", "/workflows", {"name": "On error", "nodes": [node("Error Trigger", "n8n-nodes-base.errorTrigger", 0), node("NoOp", "n8n-nodes-base.noOp", 200)],
                                          "connections": chain("Error Trigger", "NoOp")})
failing = rest("POST", "/workflows", {
    "name": "Failing",
    "settings": {"errorWorkflow": err_target["id"]},
    "nodes": [node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "fail"}, 2, webhookId="w-fail"),
              node("Boom", "n8n-nodes-base.stopAndError", 200, {"errorMessage": "=Broken for {{ $json.query.who }}"}),
              ],
    "connections": chain("Hook", "Boom"),
})
rest("POST", f"/workflows/{failing['id']}/activate")
st, body = call("GET", "/webhook/fail?who=me")
check("onReceived webhook answers immediately", st == 200 and body == {"message": "Workflow was started"})
time.sleep(1.0)
fx = rest("GET", f"/executions?workflowId={failing['id']}")[0]
check("failing execution recorded with node error", fx["status"] == "error" and wait_exec(fx["id"])["data"]["resultData"]["error"]["message"] == "Broken for me")
ex_err = rest("GET", f"/executions?workflowId={err_target['id']}")
check("error workflow was triggered", len(ex_err) == 1 and wait_exec(ex_err[0]["id"])["status"] == "success")

cont = {"name": "Continue", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
                                      node("Bad", "n8n-nodes-base.httpRequest", 200, {"url": "http://127.0.0.1:1/nothing"}, 4.2, onError="continueErrorOutput"),
                                      node("Handled", "n8n-nodes-base.noOp", 400)],
        "connections": {"Start": {"main": [[{"node": "Bad", "type": "main", "index": 0}]]}, "Bad": {"main": [[], [{"node": "Handled", "type": "main", "index": 0}]]}}}
ex = run_manual(cont)
check("continueErrorOutput routes to the error branch", ex["status"] == "success" and "error" in out(ex, "Handled")[0])

# ---------------------------------------------------------------- credentials
c = rest("POST", "/credentials", {"name": "My basic", "type": "httpBasicAuth", "data": {"user": "u", "password": "s3cret"}})
got = rest("GET", f"/credentials/{c['id']}")
check("secrets are masked in API responses", got["data"]["user"] == "u" and got["data"]["password"] != "s3cret")
rest("PATCH", f"/credentials/{c['id']}", {"data": {"user": "u2", "password": got["data"]["password"]}})
auth_wf = rest("POST", "/workflows", {"name": "Auth hook", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "secure", "authentication": "basicAuth", "responseMode": "lastNode"}, 2,
         webhookId="w-sec", credentials={"httpBasicAuth": {"id": c["id"], "name": "My basic"}})], "connections": {}})
rest("POST", f"/workflows/{auth_wf['id']}/activate")
st, _ = call("GET", "/webhook/secure")
check("webhook basic auth rejects missing credentials", st == 401)
import base64
st, body = call("GET", "/webhook/secure?x=1", headers={"authorization": "Basic " + base64.b64encode(b"u2:s3cret").decode()})
check("masked update kept the password; lastNode response", st == 200 and body["query"] == {"x": "1"}, f"{st} {body}")

# ---------------------------------------------------------------- form trigger
form = rest("POST", "/workflows", {"name": "Form", "nodes": [node("Form", "n8n-nodes-base.formTrigger", 0, {"formTitle": "Contact", "path": "contact",
    "formFields": {"values": [{"fieldLabel": "Email", "fieldType": "email", "requiredField": True}, {"fieldLabel": "Age", "fieldType": "number"}]}}, 2, webhookId="f1")],
    "connections": {}})
rest("POST", f"/workflows/{form['id']}/activate")
st, html = call("GET", "/form/contact", raw=True)
check("form page renders", st == 200 and "Contact" in html and 'name="field-0"' in html)
req = urllib.request.Request(BASE + "/form/contact", data=b"field-0=a%40b.de&field-1=42", method="POST", headers={"content-type": "application/x-www-form-urlencoded"})
with opener.open(req) as r:
    check("form submission accepted", r.status == 200)
time.sleep(0.5)
fe = wait_exec(rest("GET", f"/executions?workflowId={form['id']}")[0]["id"])
check("form data reaches the workflow", out(fe, "Form")[0]["Email"] == "a@b.de" and out(fe, "Form")[0]["Age"] == 42)

# ---------------------------------------------------------------- schedule trigger
sched = rest("POST", "/workflows", {"name": "Every second", "nodes": [node("Tick", "n8n-nodes-base.scheduleTrigger", 0, {"rule": {"interval": [{"field": "seconds", "secondsInterval": 1}]}}, 1.2),
    node("Count", "n8n-nodes-base.code", 200, {"jsCode": "const s = $getWorkflowStaticData('global'); s.n = (s.n || 0) + 1; return [{json: {n: s.n}}];"}, 2)],
    "connections": chain("Tick", "Count")})
rest("POST", f"/workflows/{sched['id']}/activate")
time.sleep(3.6)
rest("POST", f"/workflows/{sched['id']}/deactivate")
runs = rest("GET", f"/executions?workflowId={sched['id']}")
check("schedule fires repeatedly", len(runs) >= 2, f"{len(runs)}")
time.sleep(0.3)
check("static data persists between runs", rest("GET", f"/workflows/{sched['id']}")["staticData"]["global"]["n"] >= 2)

# ---------------------------------------------------------------- public API & n8n import
key = rest("POST", "/api-keys", {"label": "e2e"})["apiKey"]
st, lst = call("GET", "/api/v1/workflows", headers={"X-N8N-API-KEY": key})
check("public API with X-N8N-API-KEY", st == 200 and isinstance(lst["data"], list) and lst["nextCursor"] is None)
st, single = call("GET", f"/api/v1/workflows/{wf['id']}", headers={"X-N8N-API-KEY": key})
check("public API returns unwrapped objects", st == 200 and single["name"] == "E2E webhook")
st, _ = call("GET", "/api/v1/workflows", headers={"X-N8N-API-KEY": "wrong"})
check("invalid API key rejected", st == 401)

n8n_export = {  # as exported by n8n 1.x
    "name": "From n8n", "nodes": [
        {"parameters": {}, "id": "a1", "name": "When clicking ‘Test workflow’", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0]},
        {"parameters": {"values": {"string": [{"name": "msg", "value": "={{ 'v1 ' + 'set' }}"}]}, "options": {}}, "id": "a2", "name": "Set", "type": "n8n-nodes-base.set", "typeVersion": 2, "position": [220, 0]},
        {"parameters": {"conditions": {"string": [{"value1": "={{ $json.msg }}", "operation": "contains", "value2": "set"}]}}, "id": "a3", "name": "IF", "type": "n8n-nodes-base.if", "typeVersion": 1, "position": [440, 0]},
    ],
    "connections": {"When clicking ‘Test workflow’": {"main": [[{"node": "Set", "type": "main", "index": 0}]]}, "Set": {"main": [[{"node": "IF", "type": "main", "index": 0}]]}},
    "settings": {"executionOrder": "v1"}, "pinData": {}, "meta": {"instanceId": "x"}, "tags": [],
}
imp = rest("POST", "/workflows", n8n_export)
ex = run_manual(rest("GET", f"/workflows/{imp['id']}"))
check("legacy n8n nodes (Set v2, IF v1) run unchanged", out(ex, "IF", 0) == [{"msg": "v1 set"}], f"{ex['data']['resultData']}")

# ---------------------------------------------------------------- sandbox safety
loop = {"name": "Inf", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("Spin", "n8n-nodes-base.code", 200, {"jsCode": "while (true) {}"}, 2)],
        "connections": chain("Start", "Spin")}
st, _ = call("POST", "/rest/workflows/run", {"workflowData": loop})
check("run accepted", st == 200)
ex = rest("GET", "/executions?limit=1")[0]
r = rest("POST", f"/executions/{ex['id']}/stop") if ex["status"] == "running" else None
check("stop endpoint answers", r is None or r["stopped"] is True)

print(f"\nall {passed} checks passed")
