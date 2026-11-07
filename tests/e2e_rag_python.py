"""End-to-end checks for the vector store (RAG), the agent's vector tool, the production queue
and Python in the Code node. Run against a fresh `flowwolf:python` container started with
N8N_CONCURRENCY_PRODUCTION_LIMIT=1.

Usage: python tests/e2e_rag_python.py [base_url]
"""

import http.cookiejar
import json
import sys
import time
import urllib.request

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
call, rest, node, chain, run_manual, out, check = L.call, L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()
INNER = "http://127.0.0.1:5678"


def activate(wf):
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf


def mock(path, wid, code):
    activate(rest("POST", "/workflows", {"name": f"mock {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": path, "responseMode": "responseNode"}, 2, webhookId=wid),
        node("Code", "n8n-nodes-base.code", 200, {"jsCode": code}, 2),
        node("Respond", "n8n-nodes-base.respondToWebhook", 400, {"respondWith": "firstIncomingItem"}, 1.1)], "connections": chain("Hook", "Code", "Respond")}))


# Embeddings: 26 letter counts per text, deterministic and good enough to rank by topic.
mock("v1/embeddings", "m-emb", """const vec = (t) => { const v = new Array(26).fill(0); for (const c of String(t).toLowerCase()) { const i = c.charCodeAt(0) - 97; if (i >= 0 && i < 26) v[i]++; } return v; };
const input = [].concat($json.body.input);
return [{ json: { data: input.map((t, i) => ({ index: i, embedding: vec(t) })) } }];""")
mock("v1/chat/completions", "m-chat", """const b = $json.body; const m = b.messages; const last = m[m.length - 1];
const msg = last.role === 'tool' ? { role: 'assistant', content: 'Found: ' + last.content }
  : { role: 'assistant', content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: b.tools[0].function.name, arguments: JSON.stringify({ input: 'zzz sleepy cat naps' }) } }] };
return [{ json: { choices: [{ index: 0, message: msg }] } }];""")
oai = rest("POST", "/credentials", {"name": "emb", "type": "openAiApi", "data": {"apiKey": "sk-x", "url": f"{INNER}/webhook/v1"}})
CRED = {"openAiApi": {"id": oai["id"], "name": "emb"}}

# ---------------------------------------------------------------- vector store
docs = ["Cats purr, meow and nap all day.", "Dogs bark loudly at the mailman.", "Rust compiles fast binaries."]
ex = run_manual({"name": "insert", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Docs", "n8n-nodes-base.code", 200, {"jsCode": f"return {json.dumps(docs)}.map(t => ({{json: {{text: t, source: 'kb'}}}}))"}, 2),
    node("Store", "@flowwolf/nodes.vectorStore", 400, {"mode": "insert", "memoryKey": "kb", "text": "={{ $json.text }}", "options": {"chunkSize": 0}}, 1, credentials=CRED)],
    "connections": chain("Start", "Docs", "Store")})
check("documents are embedded and stored", ex["status"] == "success" and out(ex, "Store") == [{"store": "kb", "inserted": 3}], f"{ex['data']['resultData'].get('error')}")
ex = run_manual({"name": "search", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Search", "@flowwolf/nodes.vectorStore", 200, {"mode": "load", "memoryKey": "kb", "prompt": "meow cat nap", "topK": 2}, 1, credentials=CRED)],
    "connections": chain("Start", "Search")})
hits = out(ex, "Search")
check("similarity search ranks the matching document first", hits[0]["text"] == docs[0] and hits[0]["metadata"]["source"] == "kb" and len(hits) == 2 and hits[0]["score"] >= hits[1]["score"], f"{hits}")

ex = run_manual({"name": "rag agent", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Agent", "@n8n/n8n-nodes-langchain.agent", 200, {"provider": "openAi", "model": "m", "promptType": "define", "text": "What do cats do?",
        "tools": {"values": [{"type": "vectorStore", "name": "kb", "description": "Knowledge base", "memoryKey": "kb", "topK": 1}]}}, 1.7, credentials=CRED)],
    "connections": chain("Start", "Agent")})
res = out(ex, "Agent")[0] if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("agent answers from the knowledge base via the vector tool", res.get("output") == "Found: " + docs[0], f"{res}")

# ---------------------------------------------------------------- production queue (limit 1)
q = activate(rest("POST", "/workflows", {"name": "slow", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "GET", "path": "slow"}, 2, webhookId="w-slow"),
    node("Wait", "n8n-nodes-base.wait", 200, {"resume": "timeInterval", "amount": 1, "unit": "seconds"}, 1.1)], "connections": chain("Hook", "Wait")}))
t0 = time.time()
for _ in range(3):
    call("GET", "/webhook/slow")
time.sleep(0.4)
statuses = sorted(e["status"] for e in rest("GET", f"/executions?workflowId={q['id']}"))
check("with a limit of 1, further production runs are queued", statuses.count("queued") == 2 and statuses.count("running") == 1, f"{statuses}")
for e in rest("GET", f"/executions?workflowId={q['id']}"):
    L.wait_exec(e["id"])
check("queued runs execute one after another", time.time() - t0 >= 2.9 and all(e["status"] == "success" for e in rest("GET", f"/executions?workflowId={q['id']}")), f"{time.time() - t0:.1f}s")
ex = run_manual({"name": "manual", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0)], "connections": {}})
check("manual test runs bypass the queue", ex["status"] == "success")

# ---------------------------------------------------------------- Python in the Code node
py = {"name": "py", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
    node("Make", "n8n-nodes-base.code", 200, {"jsCode": "return [{json:{n:2}},{json:{n:5}}]"}, 2),
    node("Py", "n8n-nodes-base.code", 400, {"language": "python", "pythonCode": "import math\nprint('items', len(_input.all()))\nreturn [{'json': {'total': sum(i.json.n for i in _input.all()), 'sqrt': math.sqrt(16)}}]"}, 2),
    node("PyEach", "n8n-nodes-base.code", 600, {"language": "python", "mode": "runOnceForEachItem", "pythonCode": "_json.tripled = _json.total * 3\nreturn _item"}, 2)],
    "connections": chain("Start", "Make", "Py", "PyEach")}
ex = run_manual(py)
check("Python code runs for owners (all items + each item)", ex["status"] == "success" and out(ex, "PyEach") == [{"total": 7, "sqrt": 4.0, "tripled": 21}], f"{ex['data']['resultData'].get('error')}")
check("Python print() goes to the execution log", "items 2" in ex["data"]["logs"])

rest("POST", "/users", {"email": "dev@example.com", "password": "member pass 42", "role": "member"})
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def mcall(method, path, body=None):
    req = urllib.request.Request(L.BASE + "/rest" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"content-type": "application/json"})
    with op.open(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


mcall("POST", "/login", {"email": "dev@example.com", "password": "member pass 42"})
r = mcall("POST", "/workflows/run", {"workflowData": py})
ex = L.wait_exec(r["data"]["executionId"])
check("members may run Python (isolated runner)", ex["status"] == "success" and out(ex, "PyEach")[0]["tripled"] == 21, f"{ex['data']['resultData'].get('error')}")

# Every escape route must end in an error inside the runner, never in data from the host.
ESCAPES = r'''
import os, sys
res = {}
def attempt(name, fn):
    try:
        v = fn()
        res[name] = "LEAK: " + str(v)[:80]
    except BaseException as e:
        res[name] = "blocked"
attempt("open", lambda: open("/etc/passwd").read())
attempt("io_module", lambda: __import__("_io").FileIO("/data/encryption.key").read())
attempt("os_open", lambda: os.read(os.open("/etc/passwd", os.O_RDONLY), 50))
attempt("listdir", lambda: os.listdir("/data"))
def system_try():
    rc = os.system("id")
    if rc != 0:
        raise OSError(rc)
    return "ran"
def fork_try():
    pid = os.fork()
    if pid < 0:
        raise OSError("fork failed")
    if pid == 0:
        os._exit(0)
    return pid
attempt("system", system_try)
attempt("socket", lambda: __import__("socket").create_connection(("127.0.0.1", 5678), 2))
attempt("fork", fork_try)
attempt("kill_parent", lambda: os.kill(os.getppid(), 9) or "sent")
attempt("write", lambda: open("/tmp/x", "w").write("x"))
attempt("remove", lambda: os.remove("/data/database.sqlite"))
res["env"] = sorted(k for k in os.environ if k not in ("PWD",))
res["math"] = __import__("json").dumps({"ok": sum(range(10))})
return [{"json": res}]
'''
r = mcall("POST", "/workflows/run", {"workflowData": {"name": "escape", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
                                                                                node("Py", "n8n-nodes-base.code", 200, {"language": "python", "pythonCode": ESCAPES}, 2)],
                                                     "connections": chain("Start", "Py")}})
ex = L.wait_exec(r["data"]["executionId"])
res = out(ex, "Py")[0] if ex["status"] == "success" else ex["data"]["resultData"].get("error")
leaks = {k: v for k, v in res.items() if isinstance(v, str) and v.startswith("LEAK")} if isinstance(res, dict) else res
check("sandbox blocks files, sockets, exec, fork and kill", isinstance(res, dict) and not leaks and len([v for v in res.values() if v == "blocked"]) == 10, f"{leaks} {res}")
check("sandbox runs with an empty environment (no secrets)", isinstance(res, dict) and res["env"] == [] and res["math"] == '{"ok": 45}', f"{res}")
SUB = 'f = [c for c in ().__class__.__base__.__subclasses__() if c.__name__ == "FileIO"][0]\nreturn [{"json": {"leak": f("/etc/hostname").read()}}]'
r = mcall("POST", "/workflows/run", {"workflowData": {"name": "sub", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
                                                                             node("Py", "n8n-nodes-base.code", 200, {"language": "python", "pythonCode": SUB}, 2)],
                                                  "connections": chain("Start", "Py")}})
ex = L.wait_exec(r["data"]["executionId"])
check("__subclasses__ escape to FileIO yields an error, no data", ex["status"] == "error", f"{ex['data']['resultData']}")
r = mcall("POST", "/workflows/run", {"workflowData": {"name": "bomb", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
                                                                              node("Py", "n8n-nodes-base.code", 200, {"language": "python", "pythonCode": "x = 'a' * (4 * 1024**3)\nreturn []"}, 2)],
                                                   "connections": chain("Start", "Py")}})
ex = L.wait_exec(r["data"]["executionId"])
check("memory bomb fails in the runner, server stays up", ex["status"] == "error" and rest("GET", "/settings") is not None, f"{ex['data']['resultData'].get('error')}")

print(f"\nall {L.passed} RAG / queue / Python checks passed")
