"""End-to-end checks for OAuth2, Wait resume, declarative integrations, RSS, Postgres (TLS) and Redis.

Flowwolf mocks the external APIs itself through webhook workflows.
Usage: python tests/e2e_integrations.py [base_url] [db_host]
  db_host: host where Postgres (5433 plain, 5434 TLS) and Redis (6380) listen, as seen from the container.
"""

import json
import sys
import time
import urllib.parse
import urllib.request

sys.argv += [None] * 3
BASE = sys.argv[1] or "http://localhost:5679"
DB_HOST = sys.argv[2] or "host.docker.internal"
sys.argv = [sys.argv[0], BASE]
import e2e_lib as L  # noqa: E402

L.BASE = BASE
call, rest, node, chain, run_manual, out, check = L.call, L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()
INNER = "http://127.0.0.1:5678"


def mock(path, method, respond, webhook_id):
    """Webhook workflow that answers like an external API."""
    wf = rest("POST", "/workflows", {"name": f"mock {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": path, "responseMode": "responseNode"}, 2, webhookId=webhook_id),
        node("Respond", "n8n-nodes-base.respondToWebhook", 200, respond, 1.1)], "connections": chain("Hook", "Respond")})
    rest("POST", f"/workflows/{wf['id']}/activate")


# ---------------------------------------------------------------- mocks
mock("token", "POST", {"respondWith": "json", "responseBody": '={{ JSON.stringify({access_token: "tok-" + ($json.body.grant_type || "x"), expires_in: 3600, refresh_token: "r1"}) }}'}, "m-token")
mock("echo", "GET", {"respondWith": "firstIncomingItem"}, "m-echo")
mock("repos/:owner/:repository/issues", "POST", {"respondWith": "json", "responseBody": "={{ JSON.stringify({params: $json.params, body: $json.body, auth: $json.headers.authorization, accept: $json.headers.accept}) }}"}, "m-gh")
feed = '<rss><channel><item><title>First</title><link>https://example.com/1</link><pubDate>Tue, 05 Mar 2024 10:00:00 GMT</pubDate></item><item><title>Second</title><link>https://example.com/2</link></item></channel></rss>'
mock("feed", "GET", {"respondWith": "text", "responseBody": feed, "options": {"responseHeaders": {"entries": [{"name": "content-type", "value": "application/rss+xml"}]}}}, "m-feed")

# ---------------------------------------------------------------- OAuth2 client credentials
cc = rest("POST", "/credentials", {"name": "cc", "type": "oAuth2Api", "data": {"grantType": "clientCredentials", "accessTokenUrl": f"{INNER}/webhook/token", "clientId": "a", "clientSecret": "b", "authentication": "body"}})
ex = run_manual({"name": "oauth cc", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Call", "n8n-nodes-base.httpRequest", 200, {"url": f"{INNER}/webhook/echo", "authentication": "genericCredentialType", "genericAuthType": "oAuth2Api"}, 4.2,
         credentials={"oAuth2Api": {"id": cc["id"], "name": "cc"}})], "connections": chain("Start", "Call")})
check("OAuth2 client credentials token is fetched and sent", ex["status"] == "success" and out(ex, "Call")[0]["headers"]["authorization"] == "Bearer tok-client_credentials", f"{ex['data']['resultData'].get('error')}")
check("OAuth2 token is cached in the credential (not exposed)", "oauthTokenData" not in rest("GET", f"/credentials/{cc['id']}")["data"])

# ---------------------------------------------------------------- OAuth2 authorization code
ac = rest("POST", "/credentials", {"name": "ac", "type": "oAuth2Api", "data": {"grantType": "authorizationCode", "authUrl": "https://provider.example/auth",
                                                                              "accessTokenUrl": f"{INNER}/webhook/token", "clientId": "cid", "clientSecret": "sec", "scope": "read write"}})
check("OAuth2 credential starts disconnected", rest("GET", f"/credentials/{ac['id']}")["data"]["oauthConnected"] is False)
url = rest("GET", f"/oauth2-credential/auth?id={ac['id']}")["url"]
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
check("authorization URL carries client, scope, redirect and state", q["client_id"] == "cid" and q["scope"] == "read write" and q["redirect_uri"].endswith("/rest/oauth2-credential/callback") and len(q["state"]) > 20)
st, html = call("GET", f"/rest/oauth2-credential/callback?code=abc&state=wrong", raw=True)
check("callback rejects unknown state", "Connection failed" in html)
st, html = call("GET", f"/rest/oauth2-credential/callback?code=abc&state={q['state']}", raw=True)
check("callback exchanges the code", "Account connected" in html and rest("GET", f"/credentials/{ac['id']}")["data"]["oauthConnected"] is True, html[:300])
ex = run_manual({"name": "oauth ac", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Call", "n8n-nodes-base.httpRequest", 200, {"url": f"{INNER}/webhook/echo", "authentication": "genericCredentialType", "genericAuthType": "oAuth2Api"}, 4.2,
         credentials={"oAuth2Api": {"id": ac["id"], "name": "ac"}})], "connections": chain("Start", "Call")})
check("authorization code token is used", out(ex, "Call")[0]["headers"]["authorization"] == "Bearer tok-authorization_code")

# ---------------------------------------------------------------- Wait: resume via webhook and form
wait_wf = {"name": "wait", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Wait", "n8n-nodes-base.wait", 200, {"resume": "webhook"}, 1.1),
    node("After", "n8n-nodes-base.set", 400, {"mode": "manual", "assignments": {"assignments": [{"name": "got", "type": "string", "value": "={{ $json.body.answer }}"}]}}, 3.4)],
    "connections": chain("Start", "Wait", "After")}
r = rest("POST", "/workflows/run", {"workflowData": wait_wf})
eid = r["executionId"]
for _ in range(50):
    if rest("GET", f"/executions/{eid}")["status"] == "waiting":
        break
    time.sleep(0.1)
check("execution is parked as waiting", rest("GET", f"/executions/{eid}")["status"] == "waiting")
st, body = call("POST", f"/webhook-waiting/{eid}", {"answer": 42})
check("resume webhook accepted", st == 200)
ex = L.wait_exec(eid)
check("execution continues with the resume request", ex["status"] == "success" and out(ex, "After") == [{"got": "42"}], f"{ex['status']}")
st, _ = call("POST", f"/webhook-waiting/{eid}", {})
check("resume URL works only once", st == 404)

form_wait = {"name": "wait form", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Ask", "n8n-nodes-base.wait", 200, {"resume": "form", "formTitle": "Approve?", "formFields": {"values": [{"fieldLabel": "Decision"}]}}, 1.1)], "connections": chain("Start", "Ask")}
eid = rest("POST", "/workflows/run", {"workflowData": form_wait})["executionId"]
time.sleep(0.6)
st, page = call("GET", f"/form-waiting/{eid}", raw=True)
check("resume form renders", st == 200 and "Approve?" in page)
req = urllib.request.Request(f"{BASE}/form-waiting/{eid}", data=b"field-0=yes", method="POST", headers={"content-type": "application/x-www-form-urlencoded"})
urllib.request.urlopen(req).read()
ex = L.wait_exec(eid)
check("form answer flows into the execution", out(ex, "Ask")[0]["Decision"] == "yes")

# ---------------------------------------------------------------- declarative integration (GitHub against the mock)
gh = rest("POST", "/credentials", {"name": "gh", "type": "githubApi", "data": {"accessToken": "ghtok", "server": f"{INNER}/webhook/m-gh"}})
ex = run_manual({"name": "gh", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("GitHub", "n8n-nodes-base.github", 200, {"resource": "issue", "operation": "create", "owner": "acme", "repository": "site",
                                                 "title": "={{ 'Bug ' + 7 }}", "body": "Broken", "labels": "bug, ui"}, 1, credentials={"githubApi": {"id": gh["id"], "name": "gh"}})],
    "connections": chain("Start", "GitHub")})
res = out(ex, "GitHub")[0] if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("GitHub node builds path, JSON body, list fields and auth", res.get("params") == {"owner": "acme", "repository": "site"}
      and res["body"] == {"title": "Bug 7", "body": "Broken", "labels": ["bug", "ui"]} and res["auth"] == "Bearer ghtok" and res["accept"] == "application/vnd.github+json", f"{res}")
types = {t["name"] for t in rest("GET", "/node-types")}
check("integration catalogue is loaded", {"n8n-nodes-base.notion", "n8n-nodes-base.googleSheets", "n8n-nodes-base.stripe", "n8n-nodes-base.gmail", "n8n-nodes-base.jira"} <= types and len(types) >= 58, f"{len(types)}")

# ---------------------------------------------------------------- RSS
ex = run_manual({"name": "rss", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("RSS", "n8n-nodes-base.rssFeedRead", 200, {"url": f"{INNER}/webhook/feed"}, 1.1)],
                 "connections": chain("Start", "RSS")})
items = out(ex, "RSS")
check("RSS Read parses items", [i["title"] for i in items] == ["First", "Second"] and items[0]["isoDate"].startswith("2024-03-05"), f"{items}")

# ---------------------------------------------------------------- Postgres (plain + TLS) and Redis
def pg_flow(cred_id):
    return {"name": "pg", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
        node("Make", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{name:\"O'Brien\", n: 1}}, {json:{name:'Ada', n: 2}}]"}, 2),
        node("Insert", "n8n-nodes-base.postgres", 400, {"operation": "insert", "schema": "public", "table": "people"}, 2.5, credentials={"postgres": {"id": cred_id, "name": "pg"}}),
        node("Query", "n8n-nodes-base.postgres", 600, {"operation": "executeQuery", "query": "SELECT name, n FROM people WHERE n >= $1 ORDER BY n", "options": {"queryReplacement": "1"}}, 2.5,
             executeOnce=True, credentials={"postgres": {"id": cred_id, "name": "pg"}})],
        "connections": chain("Start", "Make", "Insert", "Query")}


def pg_setup(cred_id):
    run_manual({"name": "ddl", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("DDL", "n8n-nodes-base.postgres", 200,
               {"operation": "executeQuery", "query": "DROP TABLE IF EXISTS people; CREATE TABLE people(id serial primary key, name text, n int)"}, 2.5,
               credentials={"postgres": {"id": cred_id, "name": "pg"}})], "connections": chain("Start", "DDL")})


for label, port, ssl in (("plain", 5433, "disable"), ("TLS", 5434, "require")):
    c = rest("POST", "/credentials", {"name": "pg", "type": "postgres", "data": {"host": DB_HOST, "port": port, "database": "postgres", "user": "postgres",
                                                                                 "password": "pw with 'quote", "ssl": ssl, "allowUnauthorizedCerts": True}})
    pg_setup(c["id"])
    ex = run_manual(pg_flow(c["id"]))
    rows = out(ex, "Query") if ex["status"] == "success" else ex["data"]["resultData"]["error"]
    check(f"Postgres {label}: insert with quoting + parameterised select", rows == [{"name": "O'Brien", "n": 1}, {"name": "Ada", "n": 2}], f"{rows}")
c = rest("POST", "/credentials", {"name": "pg strict", "type": "postgres", "data": {"host": DB_HOST, "port": 5434, "database": "postgres", "user": "postgres",
                                                                                   "password": "pw with 'quote", "ssl": "require"}})
ex = run_manual(pg_flow(c["id"]))
check("Postgres TLS rejects a self-signed certificate unless allowed", ex["status"] == "error" and "certificate" in json.dumps(ex["data"]["resultData"]["error"]).lower(), f"{ex['data']['resultData'].get('error')}")

rc = rest("POST", "/credentials", {"name": "redis", "type": "redis", "data": {"host": DB_HOST, "port": 6380}})
ex = run_manual({"name": "redis", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Set", "n8n-nodes-base.redis", 200, {"operation": "set", "key": "fw:test", "value": '={{ JSON.stringify({a: 1}) }}'}, 1, credentials={"redis": {"id": rc["id"], "name": "redis"}}),
    node("Incr", "n8n-nodes-base.redis", 400, {"operation": "incr", "key": "fw:count"}, 1, credentials={"redis": {"id": rc["id"], "name": "redis"}}),
    node("Get", "n8n-nodes-base.redis", 600, {"operation": "get", "key": "fw:test", "propertyName": "val"}, 1, credentials={"redis": {"id": rc["id"], "name": "redis"}})],
    "connections": chain("Start", "Set", "Incr", "Get")})
check("Redis set/incr/get", ex["status"] == "success" and out(ex, "Get")[0]["val"] == {"a": 1}, f"{ex['data']['resultData'].get('error')}")

print(f"\nall {L.passed} integration checks passed")
