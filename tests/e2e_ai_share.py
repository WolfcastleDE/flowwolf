"""End-to-end checks for the AI Agent, Chat Trigger, template integrations and sharing/permissions.

Usage: python tests/e2e_ai_share.py [base_url]   (fresh instance)
"""

import http.cookiejar
import json
import sys
import urllib.request

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
call, rest, node, chain, run_manual, out, check = L.call, L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()
INNER = "http://127.0.0.1:5678"


def activate(wf):
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf


# ---------------------------------------------------------------- mock OpenAI-compatible API
MOCK = """const b = $json.body; const m = b.messages; const last = m[m.length - 1];
let msg;
if (last.role === 'tool') msg = { role: 'assistant', content: 'The weather is ' + last.content };
else if (b.tools && b.tools.length) msg = { role: 'assistant', content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: b.tools[0].function.name, arguments: JSON.stringify({ input: 'Berlin' }) } }] };
else msg = { role: 'assistant', content: 'msgs=' + m.length + ' system=' + (m[0].role === 'system') };
return [{ json: { choices: [{ index: 0, message: msg, finish_reason: 'stop' }] } }];"""
activate(rest("POST", "/workflows", {"name": "mock openai", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "v1/chat/completions", "responseMode": "responseNode"}, 2, webhookId="m-oai"),
    node("Code", "n8n-nodes-base.code", 200, {"jsCode": MOCK}, 2),
    node("Respond", "n8n-nodes-base.respondToWebhook", 400, {"respondWith": "firstIncomingItem"}, 1.1)], "connections": chain("Hook", "Code", "Respond")}))
oai = rest("POST", "/credentials", {"name": "mock oai", "type": "openAiApi", "data": {"apiKey": "sk-test", "url": f"{INNER}/webhook/v1"}})
CRED = {"openAiApi": {"id": oai["id"], "name": "mock oai"}}

tool_wf = rest("POST", "/workflows", {"name": "weather tool", "nodes": [
    node("In", "n8n-nodes-base.executeWorkflowTrigger", 0, {}, 1.1),
    node("Set", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "temp", "type": "string", "value": "={{ $json.query }}: 21°C"}]}}, 3.4)],
    "connections": chain("In", "Set")})

ex = run_manual({"name": "agent", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Agent", "@n8n/n8n-nodes-langchain.agent", 200, {"provider": "openAi", "model": "gpt-test", "promptType": "define", "text": "Weather in Berlin?",
        "tools": {"values": [{"type": "workflow", "name": "weather", "description": "Weather for a city", "workflowId": {"__rl": True, "value": tool_wf["id"], "mode": "list"}}]},
        "options": {"systemMessage": "You are helpful", "returnIntermediateSteps": True}}, 1.7, credentials=CRED)], "connections": chain("Start", "Agent")})
res = out(ex, "Agent")[0] if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("agent calls the workflow tool and answers with its result", "Berlin: 21°C" in res.get("output", "") and res["intermediateSteps"][0]["action"]["tool"] == "weather", f"{res}")

# ---------------------------------------------------------------- chat trigger + agent memory (active workflow)
chat = activate(rest("POST", "/workflows", {"name": "chat bot", "nodes": [
    node("Chat", "@n8n/n8n-nodes-langchain.chatTrigger", 0, {"path": "support"}, 1.1, webhookId="c1"),
    node("Agent", "@n8n/n8n-nodes-langchain.agent", 200, {"provider": "openAi", "model": "gpt-test", "promptType": "auto",
        "options": {"systemMessage": "Be brief", "memoryWindow": 5, "sessionKey": "={{ $json.sessionId }}"}}, 1.7, credentials=CRED)],
    "connections": chain("Chat", "Agent")}))
st, page = call("GET", "/chat/support", raw=True)
check("hosted chat page renders", st == 200 and "sendMessage" in page)
st, r1 = call("POST", "/chat/support", {"action": "sendMessage", "sessionId": "s1", "chatInput": "hello"})
st, r2 = call("POST", "/chat/support", {"action": "sendMessage", "sessionId": "s1", "chatInput": "again"})
st, r3 = call("POST", "/chat/support", {"action": "sendMessage", "sessionId": "other", "chatInput": "hi"})
check("chat replies come from the agent", r1 == {"output": "msgs=2 system=true"}, f"{r1}")
check("memory keeps the session history", r2 == {"output": "msgs=4 system=true"}, f"{r2}")
check("memory is separated per session", r3 == {"output": "msgs=2 system=true"}, f"{r3}")

# ---------------------------------------------------------------- template based integration (ntfy against a mock)
activate(rest("POST", "/workflows", {"name": "mock ntfy", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "ntfy", "responseMode": "responseNode"}, 2, webhookId="m-ntfy"),
    node("Respond", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": "={{ JSON.stringify({got: $json.body, auth: $json.headers.authorization || null}) }}"}, 1.1)],
    "connections": chain("Hook", "Respond")}))
nt = rest("POST", "/credentials", {"name": "ntfy", "type": "ntfyApi", "data": {"url": f"{INNER}/webhook/ntfy", "token": ""}})
ex = run_manual({"name": "ntfy", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Push", "n8n-nodes-base.ntfy", 200, {"resource": "message", "operation": "publish", "topic": "alerts", "message": "={{ 'Disk ' + 91 + '%' }}", "priority": 4, "tags": "warning, disk"}, 1,
         credentials={"ntfyApi": {"id": nt["id"], "name": "ntfy"}})], "connections": chain("Start", "Push")})
res = out(ex, "Push")[0] if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("template body: typed values, lists, dropped blanks, no empty bearer", res == {"got": {"topic": "alerts", "message": "Disk 91%", "priority": 4, "tags": ["warning", "disk"]}, "auth": None}, f"{res}")

# ---------------------------------------------------------------- Home Assistant (path params, bearer, merged JSON)
activate(rest("POST", "/workflows", {"name": "mock ha", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "api/services/:domain/:service", "responseMode": "responseNode"}, 2, webhookId="m-ha"),
    node("Respond", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": "={{ JSON.stringify({p: $json.params, b: $json.body, a: $json.headers.authorization}) }}"}, 1.1)],
    "connections": chain("Hook", "Respond")}))
ha = rest("POST", "/credentials", {"name": "ha", "type": "homeAssistantApi", "data": {"url": f"{INNER}/webhook/m-ha", "accessToken": "ha-tok"}})
ex = run_manual({"name": "ha", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("HA", "n8n-nodes-base.homeAssistant", 200, {"resource": "service", "operation": "call", "domain": "light", "service": "turn_on", "entityId": "light.kitchen",
                                                    "serviceAttributes": '{"brightness": 200}'}, 1, credentials={"homeAssistantApi": {"id": ha["id"], "name": "ha"}})],
    "connections": chain("Start", "HA")})
res = out(ex, "HA")[0] if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("Home Assistant service call: path, bearer and merged service data", res == {"p": {"domain": "light", "service": "turn_on"}, "b": {"entity_id": "light.kitchen", "brightness": 200}, "a": "Bearer ha-tok"}, f"{res}")
types = rest("GET", "/node-types")
check("node catalogue size", len(types) >= 110, f"{len(types)}")

# ---------------------------------------------------------------- sharing & permissions
rest("POST", "/users", {"email": "member@example.com", "password": "member pass 42", "firstName": "Mia", "role": "member"})
mjar = http.cookiejar.CookieJar()
mop = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(mjar))


def mcall(method, path, body=None):
    req = urllib.request.Request(L.BASE + "/rest" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"content-type": "application/json"})
    try:
        with mop.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


mcall("POST", "/login", {"email": "member@example.com", "password": "member pass 42"})
st, lst = mcall("GET", "/workflows")
check("member sees none of the owner's workflows", st == 200 and lst["data"] == [], f"{lst}")
st, _ = mcall("GET", f"/workflows/{chat['id']}")
check("member gets 404 for a foreign workflow", st == 404)
st, creds = mcall("GET", "/credentials")
check("member sees no foreign credentials", creds["data"] == [])

steal = {"name": "steal", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Agent", "@n8n/n8n-nodes-langchain.agent", 200, {"provider": "openAi", "model": "gpt-test", "promptType": "define", "text": "x"}, 1.7, credentials=CRED)],
    "connections": chain("Start", "Agent")}
st, mine = mcall("POST", "/workflows", steal)
st, r = mcall("POST", "/workflows/run", {"workflowData": mine["data"]})
ex = L.wait_exec(r["data"]["executionId"])
check("member cannot use a credential that was not shared", ex["status"] == "error" and "no access" in ex["data"]["resultData"]["error"]["message"], f"{ex['data']['resultData'].get('error')}")

member_id = [u for u in rest("GET", "/users") if u["email"] == "member@example.com"][0]["id"]
rest("PUT", f"/credentials/{oai['id']}/share", {"userIds": [member_id]})
st, r = mcall("POST", "/workflows/run", {"workflowData": mine["data"]})
ex = L.wait_exec(r["data"]["executionId"])
check("after sharing the credential the member's workflow runs", ex["status"] == "success", f"{ex['data']['resultData'].get('error')}")
st, cred = mcall("GET", f"/credentials/{oai['id']}")
check("shared credential secret stays masked", cred["data"]["data"]["apiKey"] != "sk-test")

rest("PUT", f"/workflows/{chat['id']}/share", {"userIds": [member_id]})
st, lst = mcall("GET", "/workflows")
check("shared workflow becomes visible", {w["id"] for w in lst["data"]} == {chat["id"], mine["data"]["id"]})
st, _ = mcall("DELETE", f"/workflows/{chat['id']}")
check("member cannot delete a shared workflow", st == 403)
st, _ = mcall("PUT", f"/workflows/{chat['id']}/share", {"userIds": []})
check("member cannot change sharing of a shared workflow", st == 403)
st, ex_list = mcall("GET", "/executions")
check("member only sees executions of visible workflows", all(e["workflowId"] in (chat["id"], mine["data"]["id"]) for e in ex_list["data"]) and ex_list["data"])
check("owner also sees the member's own workflow", mine["data"]["id"] in {w["id"] for w in rest("GET", "/workflows")})

print(f"\nall {L.passed} AI & sharing checks passed")
