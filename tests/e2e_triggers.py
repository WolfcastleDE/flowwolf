"""Listener triggers: MQTT (Mosquitto), Redis pub/sub, RSS feed polling, local folder watching.

Usage: python tests/e2e_triggers.py [base_url] [service_host]
Needs Mosquitto on 1883 and Redis on 6380 of service_host. Fresh Flowwolf instance.
"""

import sys
import time

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
HOST = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
rest, node, chain, run_manual, out, check = L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()


def flow(*nodes):
    return {"name": "t", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), *nodes], "connections": chain("Start", *[n["name"] for n in nodes])}


def activated(nodes, connections):
    wf = rest("POST", "/workflows", {"name": "listener", "nodes": nodes, "connections": connections})
    rest("POST", f"/workflows/{wf['id']}/activate")
    time.sleep(1.5)
    return wf["id"]


def runs(wf_id, n, timeout=20):
    for _ in range(timeout * 4):
        r = rest("GET", f"/executions?workflowId={wf_id}")
        if len(r) >= n:
            return [L.wait_exec(e["id"]) for e in r]
        time.sleep(0.25)
    return [L.wait_exec(e["id"]) for e in rest("GET", f"/executions?workflowId={wf_id}")]


# ---------------------------------------------------------------- MQTT: publish node -> trigger
mq = rest("POST", "/credentials", {"name": "mqtt", "type": "mqtt", "data": {"host": HOST, "port": 1883}})
mcred = {"mqtt": {"id": mq["id"], "name": "mqtt"}}
wid = activated([node("MQTT In", "n8n-nodes-base.mqttTrigger", 0, {"topics": "fw/#", "options": {"jsonParseBody": True}}, credentials=mcred)], {})
ex = run_manual(flow(node("Data", "n8n-nodes-base.set", 200, {"mode": "raw", "jsonOutput": '{"temp": 21.5}'}, 3.4),
                     node("Pub", "n8n-nodes-base.mqtt", 400, {"topic": "fw/kitchen", "options": {"qos": 1}}, credentials=mcred)))
check("MQTT node publishes with QoS 1", ex["status"] == "success" and out(ex, "Pub")[0]["success"] is True, f"{ex['data']['resultData'].get('error')}")
got = runs(wid, 1)
check("MQTT Trigger receives the message", got and out(got[0], "MQTT In")[0] == {"topic": "fw/kitchen", "message": {"temp": 21.5}}, f"{got and out(got[0], 'MQTT In')}")
rest("POST", f"/workflows/{wid}/deactivate")

# ---------------------------------------------------------------- Redis: publish node -> trigger
rd = rest("POST", "/credentials", {"name": "redis", "type": "redis", "data": {"host": HOST, "port": 6380}})
rcred = {"redis": {"id": rd["id"], "name": "redis"}}
wid = activated([node("Redis In", "n8n-nodes-base.redisTrigger", 0, {"channels": "orders:*", "options": {"jsonParseBody": True, "onlyMessage": True}}, credentials=rcred)], {})
ex = run_manual(flow(node("Pub", "n8n-nodes-base.redis", 200, {"operation": "publish", "channel": "orders:new", "messageData": '{"id": 7}'}, credentials=rcred)))
check("Redis publish reaches one subscriber", ex["status"] == "success" and out(ex, "Pub")[0]["receivers"] == 1, f"{ex['data']['resultData'].get('error')} {out(ex, 'Pub') if ex['status'] == 'success' else ''}")
got = runs(wid, 1)
check("Redis Trigger (pattern) starts the workflow", got and out(got[0], "Redis In")[0] == {"id": 7}, f"{got and out(got[0], 'Redis In')}")
rest("POST", f"/workflows/{wid}/deactivate")

# ---------------------------------------------------------------- RSS: a feed that grows by one item per request
feed_js = ("const s = $getWorkflowStaticData('global'); s.n = (s.n || 0) + 1;"
           "let body = ''; for (let i = 1; i <= s.n; i++) body += `<item><title>Post ${i}</title><guid>p${i}</guid><link>http://x/${i}</link></item>`;"
           "return [{json: {xml: `<?xml version=\"1.0\"?><rss version=\"2.0\"><channel><title>t</title>${body}</channel></rss>`}}];")
activated([node("Hook", "n8n-nodes-base.webhook", 0, {"path": "feed", "httpMethod": "GET", "responseMode": "responseNode"}, 2),
           node("Feed", "n8n-nodes-base.code", 200, {"jsCode": feed_js}, 2),
           node("Reply", "n8n-nodes-base.respondToWebhook", 400, {"respondWith": "text", "responseBody": "={{ $json.xml }}"}, 1.1)],
          chain("Hook", "Feed", "Reply"))
wid = activated([node("RSS", "n8n-nodes-base.rssFeedReadTrigger", 0, {"feedUrl": "http://127.0.0.1:5678/webhook/feed", "pollIntervalSeconds": 2})], {})
got = runs(wid, 2, 15)
titles = sorted(out(e, "RSS")[0]["title"] for e in got)
check("RSS Feed Trigger emits only new items, one run per poll", titles[:2] == ["Post 2", "Post 3"] and all(len(out(e, "RSS")) == 1 for e in got), f"{titles}")
rest("POST", f"/workflows/{wid}/deactivate")

# ---------------------------------------------------------------- Local File Trigger
write = lambda name, text: run_manual(flow(  # noqa: E731
    node("Text", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "t", "type": "string", "value": text}]}}, 3.4),
    node("File", "n8n-nodes-base.convertToFile", 400, {"operation": "toText", "sourceProperty": "t"}, 1.1),
    node("Write", "n8n-nodes-base.readWriteFile", 600, {"operation": "write", "fileName": name}, 1)))
ex = write("seed.txt", "x")  # makes sure the files directory exists
check("files directory is writable", ex["status"] == "success", f"{ex['data']['resultData'].get('error')}")
wid = activated([node("Watch", "n8n-nodes-base.localFileTrigger", 0, {"path": "", "events": ["add", "change"]})], {})
time.sleep(2.5)
write("new.txt", "hello")
got = runs(wid, 1, 10)
check("Local File Trigger sees a new file", got and {"path": "new.txt", "event": "add"} in out(got[0], "Watch"), f"{got and out(got[0], 'Watch')}")
time.sleep(1.2)  # mtime resolution
write("new.txt", "changed")
got = runs(wid, 2, 10)
events = sorted(i["event"] for e in got for i in out(e, "Watch"))
check("... and a changed file", events == ["add", "change"], f"{events}")
rest("POST", f"/workflows/{wid}/deactivate")

# ---------------------------------------------------------------- Shopware 6 (mocked Admin API, client credentials with templated token URL)
INNER = "http://127.0.0.1:5678"
for path, body in (("sw/api/oauth/token", '={{ JSON.stringify({access_token: "sw-" + $json.body.client_id, expires_in: 600}) }}'),
                   ("sw/api/search/product", '={{ JSON.stringify({data: [{auth: $json.headers.authorization, term: $json.body.term}]}) }}')):
    activated([node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": path, "responseMode": "responseNode"}, 2),
               node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": body}, 1.1)], chain("Hook", "Reply"))
sw = rest("POST", "/credentials", {"name": "sw", "type": "shopware6Api", "data": {"url": f"{INNER}/webhook/sw/", "clientId": "SWIA1", "clientSecret": "s"}})
ex = run_manual(flow(node("Shop", "n8n-nodes-base.shopware", 200, {"resource": "product", "operation": "search", "term": "Messer", "returnAll": True},
                          credentials={"shopware6Api": {"id": sw["id"], "name": "sw"}})))
check("Shopware 6: token from {url}/api/oauth/token, product search with bearer", ex["status"] == "success" and out(ex, "Shop") == [{"auth": "Bearer sw-SWIA1", "term": "Messer"}],
      f"{ex['data']['resultData'].get('error')} {out(ex, 'Shop') if ex['status'] == 'success' else ''}")

print(f"\nall {L.passed} trigger checks passed")
