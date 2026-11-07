"""Polling triggers against growing fake sources: the first poll only records, later polls start the
workflow once per new entry, time-filtered sources send `since`, Gmail loads each new message in full,
Google Sheets turns rows into objects, and a manual run shows what the source currently returns.

Run against a fresh instance started with the overrides:
  docker run -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_pollers.py --overrides)" ...
Usage: python tests/e2e_pollers.py [base_url]
"""

import json
import sys
import time

M = "http://127.0.0.1:5678/webhook/src"
OVERRIDES = {"https://api.notion.com/v1": "notion", "https://gmail.googleapis.com/gmail/v1/users/me": "gmail", "https://sheets.googleapis.com/v4": "sheets"}
if "--overrides" in sys.argv:
    print(",".join(f"{a}={M}/{b}" for a, b in OVERRIDES.items()))
    sys.exit()

import e2e_lib as L  # noqa: E402

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
rest, node, chain, out, check, run_manual = L.rest, L.node, L.chain, L.out, L.check, L.run_manual
L.login_or_setup()
FAST = {"item": [{"mode": "everyX", "value": 2, "unit": "seconds"}]}


def source(method, path, js, name=None):
    """A fake API whose answer is computed by JavaScript; `n` counts the calls (1, 2, 3, ...)."""
    code = "const s = $getWorkflowStaticData('global'); s.n = (s.n || 0) + 1; const n = s.n; const req = $json;\n" + js
    wf = rest("POST", "/workflows", {"name": name or f"src {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": f"src/{path}", "responseMode": "responseNode"}, 2),
        node("Answer", "n8n-nodes-base.code", 200, {"jsCode": code}, 2),
        node("Reply", "n8n-nodes-base.respondToWebhook", 400, {"respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}"}, 1.1)],
        "connections": chain("Hook", "Answer", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf["id"]


def requests_of(wf_id):
    ex = [L.wait_exec(e["id"]) for e in reversed(rest("GET", f"/executions?workflowId={wf_id}"))]
    return [out(e, "Hook")[0] for e in ex]


def cred(kind, data):
    return {kind: {"id": rest("POST", "/credentials", {"name": kind, "type": kind, "data": data})["id"], "name": kind}}


def poll_wf(name, kind, params, creds):
    wf = rest("POST", "/workflows", {"name": name, "nodes": [node("Poll", kind, 0, dict(params, pollTimes=FAST), credentials=creds), node("Seen", "n8n-nodes-base.noOp", 200)],
                                     "connections": chain("Poll", "Seen")})
    return wf["id"]


def emitted(wf_id):
    """Entries each run of the polling workflow received, oldest run first."""
    return [[i for i in out(L.wait_exec(e["id"]), "Poll")] for e in reversed(rest("GET", f"/executions?workflowId={wf_id}"))]


# ---------------------------------------------------------------- Notion: time-filtered, grows by one page per poll
notion_src = source("POST", "notion/databases/D1/query",
                    "return [{json: {results: Array.from({length: n}, (_, i) => ({id: 'p' + (i + 1), last_edited_time: '2026-11-07T10:0' + i + ':00Z'}))}}];")
wid = poll_wf("notion", "n8n-nodes-base.notionTrigger", {"databaseId": "D1"}, cred("notionApi", {"apiKey": "secret_1"}))
rest("POST", f"/workflows/{wid}/activate")
time.sleep(7.5)
rest("POST", f"/workflows/{wid}/deactivate")
runs = emitted(wid)
ids = [i["id"] for r in runs for i in r]
check("first poll only records; every later poll starts one run with exactly the new page", runs and all(len(r) == 1 for r in runs) and ids == [f"p{k}" for k in range(2, 2 + len(ids))]
      and len(ids) >= 2, f"{ids}")
reqs = requests_of(notion_src)
check("the query filters by last_edited_time on or after the previous poll", reqs and reqs[-1]["body"]["filter"]["last_edited_time"]["on_or_after"].endswith("Z")
      and reqs[-1]["headers"]["notion-version"] == "2022-06-28" and reqs[-1]["headers"]["authorization"] == "Bearer secret_1", f"{reqs[-1] if reqs else None}")
check("the poll state is kept in the node's static data", "since" in rest("GET", f"/workflows/{wid}")["staticData"]["node:Poll"]["poll"])

# ---------------------------------------------------------------- Gmail: list of ids, each new message loaded in full
source("GET", "gmail/messages", "return [{json: {messages: Array.from({length: n}, (_, i) => ({id: 'm' + (n - i)}))}}];", "gmail list")
for k in range(1, 8):
    source("GET", f"gmail/messages/m{k}", f"return [{{json: {{id: 'm{k}', snippet: 'Hello {k}', payload: {{headers: [{{name: 'Subject', value: 'Mail {k}'}}]}}}}}}];")
OA_SRC = source("POST", "tok", "return [{json: {access_token: 'tok-1', expires_in: 600}}];")
OA = {"grantType": "clientCredentials", "accessTokenUrl": f"{M}/tok", "clientId": "id", "clientSecret": "sec"}
wid = poll_wf("gmail", "n8n-nodes-base.gmailTrigger", {"q": "label:invoices"}, cred("gmailOAuth2", OA))
rest("POST", f"/workflows/{wid}/activate")
time.sleep(5.5)
rest("POST", f"/workflows/{wid}/deactivate")
got = [i for r in emitted(wid) for i in r]
check("Gmail: new messages arrive in full (expand per id)", got and got[0]["id"] == "m2" and got[0]["snippet"] == "Hello 2", f"{got}")
gl = [w for w in rest("GET", "/workflows") if w["name"] == "gmail list"][0]
lq = requests_of(gl["id"])[-1]["query"]
check("Gmail: the search keeps the user's query and adds 'after:<unix time>'", lq["q"].startswith("label:invoices after:") and lq["q"].split("after:")[1].isdigit(), f"{lq}")

# ---------------------------------------------------------------- Google Sheets: rows become objects, new rows only
source("GET", "sheets/spreadsheets/S1/values/Sheet1", "return [{json: {values: [['name', 'plan'], ...Array.from({length: n}, (_, i) => ['user' + (i + 1), i % 2 ? 'pro' : 'free'])]}}];")
wid = poll_wf("sheet", "n8n-nodes-base.googleSheetsTrigger", {"documentId": "S1", "range": "Sheet1"}, cred("googleSheetsOAuth2Api", OA))
rest("POST", f"/workflows/{wid}/activate")
time.sleep(5.5)
rest("POST", f"/workflows/{wid}/deactivate")
rows = [i for r in emitted(wid) for i in r]
check("Google Sheets: each new row arrives once as an object with row_number", rows and rows[0] == {"row_number": 3, "name": "user2", "plan": "pro"}
      and len({r["row_number"] for r in rows}) == len(rows), f"{rows}")

# ---------------------------------------------------------------- manual run shows what the source returns now
ex = run_manual({"name": "try", "nodes": [node("Poll", "n8n-nodes-base.notionTrigger", 0, {"databaseId": "D1"}, credentials=cred("notionApi", {"apiKey": "secret_2"}))],
                 "connections": {}})
check("a manual run of a polling trigger returns the current entries", ex["status"] == "success" and len(out(ex, "Poll")) >= 3 and out(ex, "Poll")[0]["id"] == "p1",
      f"{ex['data']['resultData'].get('error')}")

print(f"\nall {L.passed} polling-trigger checks passed")
