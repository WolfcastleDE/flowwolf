"""MySQL (real MariaDB), Compare Datasets, JWT, TOTP, iCal and OAuth2 PKCE.

Usage: python tests/e2e_mysql_utils.py [base_url] [db_host]   (MariaDB on port 33077, fresh Flowwolf)
"""

import base64
import hashlib
import sys
import time
import urllib.parse

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
DB_HOST = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
call, rest, node, chain, run_manual, out, check = L.call, L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()
INNER = "http://127.0.0.1:5678"


def flow(*nodes):
    return {"name": "t", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), *nodes], "connections": chain("Start", *[n["name"] for n in nodes])}


# ---------------------------------------------------------------- MySQL / MariaDB
my = rest("POST", "/credentials", {"name": "maria", "type": "mySql", "data": {"host": DB_HOST, "port": 33077, "database": "shop", "user": "root", "password": "pw with 'quote"}})
C = {"mySql": {"id": my["id"], "name": "maria"}}
ex = run_manual(flow(node("DDL", "n8n-nodes-base.mySql", 200, {"operation": "executeQuery", "query": "CREATE TABLE IF NOT EXISTS people (id INT PRIMARY KEY, name VARCHAR(50), score DECIMAL(5,2), born DATE)"}, 2.4, credentials=C)))
check("MySQL connects and runs DDL", ex["status"] == "success", f"{ex['data']['resultData'].get('error')}")
ex = run_manual(flow(
    node("Make", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{id:1, name:\"O'Brien\", score: 9.5, born: '1990-05-01'}}, {json:{id:2, name:'Ada', score: 7, born: '1815-12-10'}}]"}, 2),
    node("Insert", "n8n-nodes-base.mySql", 400, {"operation": "upsert", "table": "people"}, 2.4, credentials=C),
    node("Query", "n8n-nodes-base.mySql", 600, {"operation": "executeQuery", "query": "SELECT id, name, score, born FROM people WHERE id >= ? ORDER BY id", "options": {"queryReplacement": "1"}}, 2.4,
         executeOnce=True, credentials=C)))
rows = out(ex, "Query") if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("MySQL upsert with quotes and parameterised select", rows == [{"id": 1, "name": "O'Brien", "score": "9.50", "born": "1990-05-01"}, {"id": 2, "name": "Ada", "score": "7.00", "born": "1815-12-10"}], f"{rows}")
check("insert reports affected rows", out(ex, "Insert")[0]["affectedRows"] == 1)
ex = run_manual(flow(node("Inj", "n8n-nodes-base.mySql", 200, {"operation": "executeQuery", "query": "SELECT COUNT(*) AS n FROM people WHERE name = ?", "options": {"queryReplacement": "x' OR '1'='1"}}, 2.4, credentials=C)))
check("parameters cannot inject SQL", out(ex, "Inj") == [{"n": 0}], f"{out(ex, 'Inj')}")

# ---------------------------------------------------------------- Compare Datasets
cmp = {"name": "cmp", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("A", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{id:1,v:'x'}},{json:{id:2,v:'y'}},{json:{id:3,v:'z'}}]"}, 2),
    node("B", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{id:1,v:'x'}},{json:{id:2,v:'CHANGED'}},{json:{id:4,v:'new'}}]"}, 2),
    node("Compare", "n8n-nodes-base.compareDatasets", 400, {"mergeByFields": {"values": [{"field1": "id", "field2": "id"}]}}, 2.3)],
    "connections": {"Start": {"main": [[{"node": "A", "type": "main", "index": 0}, {"node": "B", "type": "main", "index": 0}]]},
                    "A": {"main": [[{"node": "Compare", "type": "main", "index": 0}]]}, "B": {"main": [[{"node": "Compare", "type": "main", "index": 1}]]}}}
ex = run_manual(cmp)
check("Compare Datasets: in A only / same / different / in B only",
      [out(ex, "Compare", i) for i in range(4)] == [[{"id": 3, "v": "z"}], [{"id": 1, "v": "x"}],
                                                   [{"keys": {"id": 2}, "same": {"id": 2}, "different": {"v": {"inputA": "y", "inputB": "CHANGED"}}}], [{"id": 4, "v": "new"}]],
      f"{[out(ex, 'Compare', i) for i in range(4)]}")

# ---------------------------------------------------------------- JWT, TOTP, iCal
jw = rest("POST", "/credentials", {"name": "jwt", "type": "jwtAuth", "data": {"secret": "s3cret", "algorithm": "HS256"}})
J = {"jwtAuth": {"id": jw["id"], "name": "jwt"}}
ex = run_manual(flow(node("Sign", "n8n-nodes-base.jwt", 200, {"operation": "sign", "claimsJson": '{"sub": "user-1", "role": "admin"}', "options": {"expiresIn": 60}}, 1, credentials=J),
                     node("Verify", "n8n-nodes-base.jwt", 400, {"operation": "verify", "token": "={{ $json.token }}"}, 1, credentials=J)))
check("JWT sign and verify round trip", out(ex, "Verify")[0]["sub"] == "user-1" and out(ex, "Verify")[0]["exp"] > time.time())
tok = out(ex, "Sign")[0]["token"]
h, p, s = tok.split(".")
forged = h + "." + base64.urlsafe_b64encode(b'{"sub":"user-1","role":"root"}').decode().rstrip("=") + "." + s
ex = run_manual(flow(node("Verify", "n8n-nodes-base.jwt", 200, {"operation": "verify", "token": forged}, 1, credentials=J)))
check("JWT with a forged payload is rejected", ex["status"] == "error" and "signature" in ex["data"]["resultData"]["error"]["message"])

secret = base64.b32encode(b"12345678901234567890").decode()
tp = rest("POST", "/credentials", {"name": "totp", "type": "totpApi", "data": {"secret": secret}})
ex = run_manual(flow(node("TOTP", "n8n-nodes-base.totp", 200, {"operation": "generateSecret"}, 1, credentials={"totpApi": {"id": tp["id"], "name": "totp"}})))
import hmac, struct  # noqa: E401  reference implementation for the check
counter = int(time.time()) // 30
mac = hmac.new(b"12345678901234567890", struct.pack(">Q", counter), hashlib.sha1).digest()
o = mac[-1] & 15
expected = str((struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)
check("TOTP matches a reference implementation", out(ex, "TOTP")[0]["token"] == expected, f"{out(ex, 'TOTP')} {expected}")

ex = run_manual(flow(node("ICS", "n8n-nodes-base.convertToFile", 200, {"operation": "iCal", "title": "Launch, party", "start": "2026-12-24T18:00:00+01:00", "end": "2026-12-24T20:00:00+01:00",
                                                                     "additionalFields": {"location": "Berlin"}}, 1.1)))
item = ex["data"]["resultData"]["runData"]["ICS"][0]["data"]["main"][0][0]
ics = base64.b64decode(item["binary"]["data"]["data"]).decode()
check("iCal event file", item["binary"]["data"]["mimeType"] == "text/calendar" and "DTSTART:20261224T170000Z" in ics and "SUMMARY:Launch\\, party" in ics and "LOCATION:Berlin" in ics, ics)

# ---------------------------------------------------------------- OAuth2 PKCE
# Token endpoint only answers when the verifier matches the challenge sent before.
L.rest("POST", f"/workflows/{rest('POST', '/workflows', {'name': 'mock pkce', 'nodes': [node('Hook', 'n8n-nodes-base.webhook', 0, {'httpMethod': 'POST', 'path': 'pkce-token', 'responseMode': 'responseNode'}, 2, webhookId='m-pkce'), node('Respond', 'n8n-nodes-base.respondToWebhook', 200, {'respondWith': 'json', 'responseBody': '={{ JSON.stringify($json.body.code_verifier ? {access_token: \"pkce-ok\", verifier: $json.body.code_verifier} : {error: \"no verifier\"}) }}'}, 1.1)], 'connections': chain('Hook', 'Respond')})['id']}/activate")
cr = rest("POST", "/credentials", {"name": "pkce", "type": "oAuth2Api", "data": {"grantType": "pkce", "authUrl": "https://idp.example/authorize", "accessTokenUrl": f"{INNER}/webhook/pkce-token", "clientId": "c"}})
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(rest("GET", f"/oauth2-credential/auth?id={cr['id']}")["url"]).query))
check("PKCE challenge is sent (S256)", q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) == 43)
st, html = call("GET", f"/rest/oauth2-credential/callback?code=abc&state={q['state']}", raw=True)
check("token exchange sends the code verifier", "Account connected" in html, html[:200])

print(f"\nall {L.passed} MySQL / utility / PKCE checks passed")
