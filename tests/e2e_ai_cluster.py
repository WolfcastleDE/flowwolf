"""AI cluster nodes in n8n's format: an AI Agent and chains with sub-nodes connected through ai_*
connections (chat models, memory, tools incl. MCP, output parsers, embeddings, document loader,
vector stores, retriever). Models and the MCP server are Flowwolf webhook workflows; Postgres and
Redis memory and the Qdrant vector store run against real containers.

Usage: python tests/e2e_ai_cluster.py [base_url] [service_host]
  service_host: where Postgres (5433), pgvector Postgres (5436), Redis (6380) and Qdrant (6333) listen,
  as seen from the container. Start the instance with the Pinecone overrides:
  docker run -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_ai_cluster.py --overrides)" ...
"""

import base64
import json
import sys
import time
import urllib.error
import urllib.request

if "--overrides" in sys.argv:
    print("https://api.pinecone.io=http://127.0.0.1:5678/webhook/pcctl,https://idx.mock=http://127.0.0.1:5678/webhook/pcidx")
    sys.exit()

import e2e_lib as L  # noqa: E402

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
HOST = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
rest, node, chain, out, check, run_manual = L.rest, L.node, L.chain, L.out, L.check, L.run_manual
L.login_or_setup()
M = "http://127.0.0.1:5678/webhook"
RUN = int(time.time())
LC = "@n8n/n8n-nodes-langchain."


def mock(method, path, js):
    """A fake API answered by JavaScript (`b` is the request body); records calls as executions."""
    wf = rest("POST", "/workflows", {"name": f"mock {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": path, "responseMode": "responseNode"}, 2),
        node("Answer", "n8n-nodes-base.code", 200, {"jsCode": "const b = $json.body; const h = $json.headers;\n" + js}, 2),
        node("Reply", "n8n-nodes-base.respondToWebhook", 400, {"respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}"}, 1.1)],
        "connections": chain("Hook", "Answer", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")
    return wf["id"]


def bodies(wf_id):
    return [out(L.wait_exec(e["id"]), "Hook")[0] for e in reversed(rest("GET", f"/executions?workflowId={wf_id}"))]


# A small OpenAI-compatible chat model that behaves differently per task.
LLM_JS = r"""
const msgs = b.messages; const last = msgs[msgs.length - 1];
const sys = (msgs.find((m) => m.role === 'system') || {}).content || '';
const user = String(([...msgs].reverse().find((m) => m.role === 'user') || {}).content || '');
let msg;
if (b.tools && b.tools.length && last.role !== 'tool') {
  const names = b.tools.map((t) => t.function.name);
  const pick = names.includes('calculator') ? 'calculator' : names[0];
  const args = pick === 'calculator' ? {input: '2+3*4'} : pick === 'get_weather' ? {city: 'Berlin'} : {input: 'fruit'};
  msg = {role: 'assistant', content: null, tool_calls: [{id: 'call_1', type: 'function', function: {name: pick, arguments: JSON.stringify(args)}}]};
} else if (last.role === 'tool') {
  msg = {role: 'assistant', content: 'Result: ' + last.content};
} else if (user.includes('Categories:')) {
  msg = {role: 'assistant', content: user.includes('terrible') ? '{"sentiment": "Negative"}' : '{"sentiment": "Positive"}'};
} else if (user.includes('Classify the text')) {
  msg = {role: 'assistant', content: user.includes('invoice') ? '{"categories": ["billing"]}' : '{"categories": ["support"]}'};
} else if (user.includes('Extract the information')) {
  msg = {role: 'assistant', content: 'Here you go: {"name": "Ada", "age": 36}'};
} else if (user.includes('CONCISE SUMMARY')) {
  msg = {role: 'assistant', content: 'A short summary'};
} else if (user.includes('shaped like this example')) {
  msg = {role: 'assistant', content: '```json\n{"city": "Berlin"}\n```'};
} else if (sys.includes('pieces of context')) {
  msg = {role: 'assistant', content: 'From context: ' + sys.split('----------------\n')[1]};
} else {
  msg = {role: 'assistant', content: 'History: ' + msgs.filter((m) => m.role === 'user').length};
}
return [{json: {choices: [{message: msg}]}}];
"""
llm = mock("POST", "llm/chat/completions", LLM_JS)
# Embeddings: letter frequencies, so similar words end up close.
mock("POST", "llm/embeddings", r"""
const vec = (t) => { const v = Array(26).fill(0); for (const c of String(t).toLowerCase()) { const i = c.charCodeAt(0) - 97; if (i >= 0 && i < 26) v[i]++; } return v.map((x) => x + 0.01); };
const input = Array.isArray(b.input) ? b.input : [b.input];
return [{json: {data: input.map((t, i) => ({index: i, embedding: vec(t)}))}}];
""")
anth = mock("POST", "anth/v1/messages", "return [{json: {content: [{type: 'text', text: 'Hi from Claude ' + b.model}], stop_reason: 'end_turn'}}];")
mcp = mock("POST", "mcp", r"""
let result = {};
if (b.method === 'initialize') result = {protocolVersion: '2025-03-26', capabilities: {tools: {}}, serverInfo: {name: 'fake', version: '1'}};
if (b.method === 'tools/list') result = {tools: [{name: 'get_weather', description: 'Weather for a city', inputSchema: {type: 'object', properties: {city: {type: 'string'}}, required: ['city']}}]};
if (b.method === 'tools/call') result = {content: [{type: 'text', text: 'Sunny in ' + b.params.arguments.city}]};
return [{json: {jsonrpc: '2.0', id: b.id ?? null, result}}];
""")

OPENAI = {"openAiApi": {"id": rest("POST", "/credentials", {"name": "fake openai", "type": "openAiApi", "data": {"apiKey": "sk-test", "url": f"{M}/llm"}})["id"], "name": "fake openai"}}
ANTHROPIC = {"anthropicApi": {"id": rest("POST", "/credentials", {"name": "fake anthropic", "type": "anthropicApi", "data": {"apiKey": "sk-ant", "url": f"{M}/anth"}})["id"], "name": "fake anthropic"}}


def ai(src, dst, kind):
    return {src: {kind: [[{"node": dst, "type": kind, "index": 0}]]}}


def wf(nodes, main, *ai_links):
    conns = dict(chain(*main))
    for link in ai_links:
        for k, v in link.items():
            conns.setdefault(k, {}).update(v)
    return {"name": "ai", "nodes": nodes, "connections": conns}


START = node("Start", "n8n-nodes-base.manualTrigger", 0)
MODEL = node("Model", LC + "lmChatOpenAi", 300, {"model": "gpt-4o-mini"}, credentials=OPENAI)


def text_item(name, *texts):
    return node(name, "n8n-nodes-base.code", 150, {"jsCode": f"return {json.dumps([{'json': {'text': t}} for t in texts])};"}, 2)


# ---------------------------------------------------------------- AI Agent with tools
ex = run_manual(wf([START, node("Agent", LC + "agent", 200, {"promptType": "define", "text": "What is 2+3*4?", "options": {"returnIntermediateSteps": True}}, 1.7), MODEL,
                    node("Calc", LC + "toolCalculator", 400), node("Code Tool", LC + "toolCode", 500, {"name": "shout", "description": "Upper-cases text", "jsCode": "return query.toUpperCase();"})],
                   ["Start", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("Calc", "Agent", "ai_tool"), ai("Code Tool", "Agent", "ai_tool")))
o = out(ex, "Agent")[0] if ex["status"] == "success" else ex["data"]["resultData"].get("error")
check("Agent runs the connected calculator tool and answers", isinstance(o, dict) and o["output"] == "Result: 14" and o["intermediateSteps"][0]["action"]["tool"] == "calculator", f"{o}")
req = bodies(llm)[-1]["body"]
check("the model sees both tool sub-nodes with their schemas", sorted(t["function"]["name"] for t in req["tools"]) == ["calculator", "shout"]
      and req["tools"][0]["function"]["parameters"]["required"] == ["input"], f"{req.get('tools')}")

# ---------------------------------------------------------------- memory: simple, Postgres, Redis (production runs keep state)
PG = {"postgres": {"id": rest("POST", "/credentials", {"name": "pg", "type": "postgres", "data": {"host": HOST, "port": 5433, "database": "postgres", "user": "postgres",
                                                                                                    "password": "pw with 'quote", "ssl": "disable"}})["id"], "name": "pg"}}
REDIS = {"redis": {"id": rest("POST", "/credentials", {"name": "rd", "type": "redis", "data": {"host": HOST, "port": 6380}})["id"], "name": "rd"}}
for kind, creds, extra in (("memoryBufferWindow", None, {}), ("memoryPostgresChat", PG, {"tableName": "chat_test"}), ("memoryRedisChat", REDIS, {})):
    mem = node("Memory", LC + kind, 400, dict({"sessionIdType": "customKey", "sessionKey": f"s-{kind}-{RUN}", "contextWindowLength": 5}, **extra), credentials=creds) if creds else \
        node("Memory", LC + kind, 400, {"sessionIdType": "customKey", "sessionKey": "s1"})
    w = rest("POST", "/workflows", dict(wf([node("Hook", "n8n-nodes-base.webhook", 0, {"path": f"chat-{kind}", "httpMethod": "POST", "responseMode": "lastNode"}, 2),
                                            node("Agent", LC + "agent", 200, {"promptType": "define", "text": "={{ $json.body.q }}"}, 1.7), MODEL, mem],
                                           ["Hook", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("Memory", "Agent", "ai_memory")), name=f"chat {kind}"))
    rest("POST", f"/workflows/{w['id']}/activate")
    answers = []
    for q in ("Hi", "And again"):
        r = urllib.request.urlopen(urllib.request.Request(f"{L.BASE}/webhook/chat-{kind}", data=json.dumps({"q": q}).encode(), headers={"content-type": "application/json"}), timeout=60)
        answers.append(json.loads(r.read())["output"])
    check(f"{kind}: the second turn sees the first one", answers == ["History: 1", "History: 2"], f"{answers}")

# ---------------------------------------------------------------- chains
ex = run_manual(wf([START, node("Chain", LC + "chainLlm", 200, {"promptType": "define", "text": "Where is the Brandenburg Gate?", "hasOutputParser": True}, 1.5), MODEL,
                    node("Parser", LC + "outputParserStructured", 500, {"jsonSchemaExample": '{"city": "Paris"}'})],
                   ["Start", "Chain"], ai("Model", "Chain", "ai_languageModel"), ai("Parser", "Chain", "ai_outputParser")))
check("Basic LLM Chain with a structured output parser returns parsed JSON", ex["status"] == "success" and out(ex, "Chain")[0] == {"output": {"city": "Berlin"}},
      f"{ex['data']['resultData'].get('error')}")
ex = run_manual(wf([START, text_item("Texts", "What a great product", "This was terrible"), node("Sentiment", LC + "sentimentAnalysis", 300, {"inputText": "={{ $json.text }}"}, 1), MODEL],
                   ["Start", "Texts", "Sentiment"], ai("Model", "Sentiment", "ai_languageModel")))
runs = ex["data"]["resultData"]["runData"]["Sentiment"][0]["data"]["main"] if ex["status"] == "success" else []
check("Sentiment Analysis routes items to the category branch", len(runs) == 3 and runs[0][0]["json"]["sentimentAnalysis"] == {"category": "Positive"}
      and runs[1] == [] and runs[2][0]["json"]["text"] == "This was terrible", f"{ex['data']['resultData'].get('error')} {runs}")
ex = run_manual(wf([START, text_item("Texts", "Where is my invoice?", "The app crashes"),
                    node("Classify", LC + "textClassifier", 300, {"inputText": "={{ $json.text }}", "categories": {"categories": [{"category": "billing", "description": "money"},
                                                                                                                                   {"category": "support", "description": "problems"}]}}, 1), MODEL],
                   ["Start", "Texts", "Classify"], ai("Model", "Classify", "ai_languageModel")))
runs = ex["data"]["resultData"]["runData"]["Classify"][0]["data"]["main"] if ex["status"] == "success" else []
check("Text Classifier sends each item to its category output", len(runs) == 2 and runs[0][0]["json"]["text"] == "Where is my invoice?" and runs[1][0]["json"]["text"] == "The app crashes",
      f"{ex['data']['resultData'].get('error')} {runs}")
ex = run_manual(wf([START, text_item("Texts", "Ada is 36 years old"), node("Extract", LC + "informationExtractor", 300, {"text": "={{ $json.text }}", "attributes": {"attributes": [
    {"name": "name", "type": "string", "description": "Name"}, {"name": "age", "type": "number", "description": "Age"}]}}, 1), MODEL],
                   ["Start", "Texts", "Extract"], ai("Model", "Extract", "ai_languageModel")))
check("Information Extractor returns the attributes", ex["status"] == "success" and out(ex, "Extract")[0] == {"output": {"name": "Ada", "age": 36}}, f"{ex['data']['resultData'].get('error')}")
ex = run_manual(wf([START, text_item("Texts", "Long text one", "Long text two"), node("Summary", LC + "chainSummarization", 300, {}, 2), MODEL],
                   ["Start", "Texts", "Summary"], ai("Model", "Summary", "ai_languageModel")))
check("Summarization Chain summarises the input", ex["status"] == "success" and out(ex, "Summary")[0] == {"response": {"text": "A short summary"}}, f"{ex['data']['resultData'].get('error')}")
ex = run_manual(wf([START, node("Chain", LC + "chainLlm", 200, {"promptType": "define", "text": "Hello"}, 1.5), node("Claude", LC + "lmChatAnthropic", 300, {"model": "claude-test"}, credentials=ANTHROPIC)],
                   ["Start", "Chain"], ai("Claude", "Chain", "ai_languageModel")))
check("Anthropic chat model sub-node", ex["status"] == "success" and out(ex, "Chain")[0] == {"text": "Hi from Claude claude-test"}, f"{ex['data']['resultData'].get('error')}")

# ---------------------------------------------------------------- RAG: insert documents, Q&A chain over a retriever, store as agent tool
EMB = node("Embeddings", LC + "embeddingsOpenAi", 500, {"model": "text-embedding-3-small"}, credentials=OPENAI)
LOADER = node("Loader", LC + "documentDefaultDataLoader", 600, {"jsonMode": "expressionData", "jsonData": "={{ $json.text }}"})
ex = run_manual(wf([START, text_item("Docs", "Apples are red and crunchy", "Bananas are yellow and soft"),
                    node("Store", LC + "vectorStoreInMemory", 300, {"mode": "insert", "memoryKey": "fruit", "clearStore": True}, 1), EMB, LOADER],
                   ["Start", "Docs", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
check("Simple Vector Store inserts documents through loader and embeddings", ex["status"] == "success" and len(out(ex, "Store")) == 2, f"{ex['data']['resultData'].get('error')}")
ex = run_manual(wf([START, node("QA", LC + "chainRetrievalQa", 200, {"promptType": "define", "text": "Bananas yellow soft?"}, 1.4), MODEL,
                    node("Retriever", LC + "retrieverVectorStore", 400, {"topK": 1}), node("Store", LC + "vectorStoreInMemory", 500, {"mode": "retrieve", "memoryKey": "fruit"}, 1), EMB],
                   ["Start", "QA"], ai("Model", "QA", "ai_languageModel"), ai("Retriever", "QA", "ai_retriever"), ai("Store", "Retriever", "ai_vectorStore"), ai("Embeddings", "Store", "ai_embedding")))
check("Q&A chain answers from the retrieved document", ex["status"] == "success" and out(ex, "QA")[0]["response"].startswith("From context: ")
      and "Banana" in out(ex, "QA")[0]["response"], f"{ex['data']['resultData'].get('error')} {out(ex, 'QA') if ex['status'] == 'success' else ''}")
ex = run_manual(wf([START, node("Agent", LC + "agent", 200, {"promptType": "define", "text": "Tell me about fruit"}, 1.7), MODEL,
                    node("Knowledge", LC + "vectorStoreInMemory", 400, {"mode": "retrieve-as-tool", "memoryKey": "fruit", "toolName": "fruit_facts", "topK": 2}, 1), EMB],
                   ["Start", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("Knowledge", "Agent", "ai_tool"), ai("Embeddings", "Knowledge", "ai_embedding")))
check("a vector store connected as tool feeds documents to the agent", ex["status"] == "success" and "crunchy" in out(ex, "Agent")[0]["output"], f"{ex['data']['resultData'].get('error')}")
QD = {"qdrantApi": {"id": rest("POST", "/credentials", {"name": "qd", "type": "qdrantApi", "data": {"qdrantUrl": f"http://{HOST}:6333"}})["id"], "name": "qd"}}
ex = run_manual(wf([START, text_item("Docs", "Cherries are small and red", "Lemons are sour"),
                    node("Store", LC + "vectorStoreQdrant", 300, {"mode": "insert", "qdrantCollection": "fruit_test", "clearStore": True}, 1, credentials=QD), EMB, LOADER],
                   ["Start", "Docs", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
ok_insert = ex["status"] == "success"
ex = run_manual(wf([START, node("Store", LC + "vectorStoreQdrant", 300, {"mode": "load", "qdrantCollection": "fruit_test", "prompt": "lemon", "topK": 1}, 1, credentials=QD), EMB],
                   ["Start", "Store"], ai("Embeddings", "Store", "ai_embedding")))
check("Qdrant vector store: insert, then the nearest document is found", ok_insert and ex["status"] == "success" and out(ex, "Store")[0]["document"]["pageContent"] == "Lemons are sour",
      f"{ex['data']['resultData'].get('error')} {out(ex, 'Store') if ex['status'] == 'success' else ''}")

# ---------------------------------------------------------------- MCP client tool
ex = run_manual(wf([START, node("Agent", LC + "agent", 200, {"promptType": "define", "text": "Weather in Berlin?"}, 1.7), MODEL,
                    node("MCP", LC + "mcpClientTool", 400, {"endpointUrl": f"{M}/mcp"}, 1)],
                   ["Start", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("MCP", "Agent", "ai_tool")))
check("MCP Client Tool offers the server's tools and calls them", ex["status"] == "success" and out(ex, "Agent")[0]["output"] == "Result: Sunny in Berlin",
      f"{ex['data']['resultData'].get('error')} {out(ex, 'Agent') if ex['status'] == 'success' else ''}")
methods = [b["body"]["method"] for b in bodies(mcp)]
check("MCP handshake, listing and call follow the protocol", methods[:3] == ["initialize", "notifications/initialized", "tools/list"] and "tools/call" in methods, f"{methods}")

# ---------------------------------------------------------------- documents from files (CSV, PDF)
def tiny_pdf(text):
    """A one-page PDF with a standard font, built by hand."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return pdf + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


files = [{"json": {"file": "cities.csv"}, "binary": {"data": {"data": base64.b64encode(b"name,city\nAda,Berlin\n").decode(), "mimeType": "text/csv", "fileName": "cities.csv"}}},
         {"json": {"file": "doc.pdf"}, "binary": {"data": {"data": base64.b64encode(tiny_pdf("Flowwolf reads PDFs")).decode(), "mimeType": "application/pdf", "fileName": "doc.pdf"}}}]
BIN_LOADER = node("Loader", LC + "documentDefaultDataLoader", 600, {"dataType": "binary", "loader": "auto", "binaryDataKey": "data"})
ex = run_manual(wf([START, node("Files", "n8n-nodes-base.code", 150, {"jsCode": f"return {json.dumps(files)};"}, 2),
                    node("Store", LC + "vectorStoreInMemory", 300, {"mode": "insert", "memoryKey": "files", "clearStore": True}, 1), EMB, BIN_LOADER],
                   ["Start", "Files", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
ok_files = ex["status"] == "success"
ex2 = run_manual(wf([START, node("Store", LC + "vectorStoreInMemory", 300, {"mode": "load", "memoryKey": "files", "prompt": "anything", "topK": 5}, 1), EMB],
                    ["Start", "Store"], ai("Embeddings", "Store", "ai_embedding")))
texts = [d["document"]["pageContent"] for d in out(ex2, "Store")] if ex2["status"] == "success" else []
check("Default Data Loader reads CSV and PDF files", ok_files and any("city: Berlin" in t for t in texts) and any("Flowwolf reads PDFs" in t for t in texts),
      f"{ex['data']['resultData'].get('error')} {texts}")

# ---------------------------------------------------------------- PGVector (real pgvector), Pinecone and Supabase (fakes)
PGV = {"postgres": {"id": rest("POST", "/credentials", {"name": "pgv", "type": "postgres", "data": {"host": HOST, "port": 5436, "database": "postgres", "user": "postgres",
                                                                                                     "password": "pw", "ssl": "disable"}})["id"], "name": "pgv"}}
ex = run_manual(wf([START, text_item("Docs", "Grapes grow in bunches", "Melons are large"),
                    node("Store", LC + "vectorStorePGVector", 300, {"mode": "insert", "tableName": f"vec_{RUN}"}, 1, credentials=PGV), EMB, LOADER],
                   ["Start", "Docs", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
ok_ins = ex["status"] == "success"
ex = run_manual(wf([START, node("Store", LC + "vectorStorePGVector", 300, {"mode": "load", "tableName": f"vec_{RUN}", "prompt": "Melons large", "topK": 1}, 1, credentials=PGV), EMB],
                   ["Start", "Store"], ai("Embeddings", "Store", "ai_embedding")))
check("PGVector: documents go into Postgres and the nearest one comes back", ok_ins and ex["status"] == "success" and out(ex, "Store")[0]["document"]["pageContent"] == "Melons are large"
      and 0 < out(ex, "Store")[0]["score"] <= 1, f"{ex['data']['resultData'].get('error')} {out(ex, 'Store') if ex['status'] == 'success' else ''}")

mock("GET", "pcctl/indexes/fruit-idx", "return [{json: {name: 'fruit-idx', host: 'idx.mock'}}];")
mock("POST", "pcidx/vectors/delete", "return [{json: {}}];")
up = mock("POST", "pcidx/vectors/upsert", "return [{json: {upsertedCount: b.vectors.length}}];")
mock("POST", "pcidx/query", "return [{json: {matches: [{id: 'v1', score: 0.91, metadata: {text: 'Kiwis are green', source: 'test'}}]}}];")
PC = {"pineconeApi": {"id": rest("POST", "/credentials", {"name": "pc", "type": "pineconeApi", "data": {"apiKey": "pck"}})["id"], "name": "pc"}}
ex = run_manual(wf([START, text_item("Docs", "Kiwis are green"),
                    node("Store", LC + "vectorStorePinecone", 300, {"mode": "insert", "pineconeIndex": "fruit-idx", "options": {"pineconeNamespace": "ns1", "clearNamespace": True}}, 1, credentials=PC),
                    EMB, LOADER], ["Start", "Docs", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
ub = bodies(up)[-1] if ex["status"] == "success" else {}
ex = run_manual(wf([START, node("Store", LC + "vectorStorePinecone", 300, {"mode": "load", "pineconeIndex": "fruit-idx", "prompt": "kiwi", "options": {"pineconeNamespace": "ns1"}}, 1, credentials=PC), EMB],
                   ["Start", "Store"], ai("Embeddings", "Store", "ai_embedding")))
check("Pinecone: index host lookup, upsert into the namespace, query with metadata", ub and ub["body"]["namespace"] == "ns1" and ub["body"]["vectors"][0]["metadata"]["text"] == "Kiwis are green"
      and len(ub["body"]["vectors"][0]["values"]) == 26 and ub["headers"]["api-key"] == "pck" and ex["status"] == "success"
      and out(ex, "Store")[0]["document"] == {"pageContent": "Kiwis are green", "metadata": {"source": "test"}}, f"{ub} {ex['data']['resultData'].get('error')}")

sb_ins = mock("POST", "supa/rest/v1/documents", "return [{json: {}}];")
sb_rpc = rest("POST", "/workflows", {"name": "supa rpc", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "supa/rest/v1/rpc/match_documents", "responseMode": "responseNode"}, 2),
    node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": '[{"content": "Plums are purple", "metadata": {"k": 1}, "similarity": 0.8}]'}, 1.1)],
    "connections": chain("Hook", "Reply")})
rest("POST", f"/workflows/{sb_rpc['id']}/activate")
SB = {"supabaseApi": {"id": rest("POST", "/credentials", {"name": "sb", "type": "supabaseApi", "data": {"host": f"{M}/supa", "serviceRole": "srk"}})["id"], "name": "sb"}}
ex = run_manual(wf([START, text_item("Docs", "Plums are purple"), node("Store", LC + "vectorStoreSupabase", 300, {"mode": "insert", "tableName": "documents"}, 1, credentials=SB), EMB, LOADER],
                   ["Start", "Docs", "Store"], ai("Embeddings", "Store", "ai_embedding"), ai("Loader", "Store", "ai_document")))
sbb = bodies(sb_ins)[-1] if ex["status"] == "success" else {}
ex = run_manual(wf([START, node("Store", LC + "vectorStoreSupabase", 300, {"mode": "load", "tableName": "documents", "prompt": "plum"}, 1, credentials=SB), EMB],
                   ["Start", "Store"], ai("Embeddings", "Store", "ai_embedding")))
check("Supabase: rows with embedding via PostgREST, search via match_documents", sbb and sbb["headers"]["apikey"] == "srk" and sbb["body"][0]["content"] == "Plums are purple"
      and len(sbb["body"][0]["embedding"]) == 26 and ex["status"] == "success" and out(ex, "Store")[0]["document"]["pageContent"] == "Plums are purple",
      f"{sbb} {ex['data']['resultData'].get('error')}")

# ---------------------------------------------------------------- MCP Server Trigger
def rpc(path, method, params=None, rid=1, headers=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        body["id"] = rid
    req = urllib.request.Request(f"{L.BASE}/mcp/{path}", data=json.dumps(body).encode(), headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read()
            return r.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        return e.code, None


srv = rest("POST", "/workflows", wf([node("MCP Server", LC + "mcpTrigger", 0, {"path": f"tools{RUN}"}, 1), node("Calc", LC + "toolCalculator", 100),
                                     node("Shout", LC + "toolCode", 200, {"name": "shout", "description": "Upper-cases text", "jsCode": "return query.toUpperCase();"})],
                                    [], ai("Calc", "MCP Server", "ai_tool"), ai("Shout", "MCP Server", "ai_tool")))
rest("POST", f"/workflows/{srv['id']}/activate")
_, init = rpc(f"tools{RUN}", "initialize", {"protocolVersion": "2025-03-26"})
st_note, _ = rpc(f"tools{RUN}", "notifications/initialized", rid=None)
_, listed = rpc(f"tools{RUN}", "tools/list", rid=2)
_, called = rpc(f"tools{RUN}", "tools/call", {"name": "calculator", "arguments": {"input": "6*7"}}, rid=3)
check("MCP Server Trigger: initialize, tools/list and tools/call", init["result"]["serverInfo"]["name"] == "flowwolf" and st_note == 202
      and sorted(t["name"] for t in listed["result"]["tools"]) == ["calculator", "shout"] and called["result"]["content"][0]["text"] == "42", f"{listed} {called}")
BEARER_CRED = {"httpBearerAuth": {"id": rest("POST", "/credentials", {"name": "mcp token", "type": "httpBearerAuth", "data": {"token": "t0k"}})["id"], "name": "mcp token"}}
sec = rest("POST", "/workflows", wf([node("MCP Server", LC + "mcpTrigger", 0, {"path": f"secured{RUN}", "authentication": "bearerAuth"}, 1, credentials=BEARER_CRED),
                                     node("Calc", LC + "toolCalculator", 100)], [], ai("Calc", "MCP Server", "ai_tool")))
rest("POST", f"/workflows/{sec['id']}/activate")
st_no, _ = rpc(f"secured{RUN}", "tools/list")
st_yes, _ = rpc(f"secured{RUN}", "tools/list", headers={"authorization": "Bearer t0k"})
check("MCP Server Trigger: bearer authentication", st_no == 401 and st_yes == 200, f"{st_no} {st_yes}")
ex = run_manual(wf([START, node("Agent", LC + "agent", 200, {"promptType": "define", "text": "What is 2+3*4?"}, 1.7), MODEL,
                    node("MCP", LC + "mcpClientTool", 400, {"endpointUrl": f"http://127.0.0.1:5678/mcp/tools{RUN}"}, 1)],
                   ["Start", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("MCP", "Agent", "ai_tool")))
check("an agent uses Flowwolf's own MCP server through the MCP client", ex["status"] == "success" and out(ex, "Agent")[0]["output"] == "Result: 14", f"{ex['data']['resultData'].get('error')}")

# ---------------------------------------------------------------- MCP over HTTP+SSE (2024-11-05 transport)
import http.client
import urllib.parse


def sse_open(path, headers=None):
    u = urllib.parse.urlparse(L.BASE)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    c.request("GET", f"/mcp/{path}/sse", headers={"accept": "text/event-stream", **(headers or {})})
    return c, c.getresponse()


def sse_next(r):
    event, data = "message", []
    while True:
        line = r.fp.readline().decode().rstrip("\r\n")
        if line == "":
            if data:
                return event, "\n".join(data)
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


c, r = sse_open(f"tools{RUN}")
ev, endpoint = sse_next(r)
post = urllib.request.Request(f"{L.BASE}{endpoint}", headers={"content-type": "application/json"},
                              data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "shout", "arguments": {"input": "hi"}}}).encode())
with urllib.request.urlopen(post, timeout=30) as pr:
    st_post = pr.status
ev2, msg = sse_next(r)
c.close()
check("MCP Server Trigger over SSE: endpoint event, POST 202, answer on the stream",
      r.status == 200 and ev == "endpoint" and "/messages?sessionId=" in endpoint and st_post == 202 and ev2 == "message"
      and json.loads(msg)["id"] == 7 and json.loads(msg)["result"]["content"][0]["text"] == "HI", f"{r.status} {ev} {endpoint} {st_post} {ev2} {msg}")
c, r = sse_open(f"secured{RUN}")
st_sse_no = r.status
c.close()
c, r = sse_open(f"secured{RUN}", {"authorization": "Bearer t0k"})
st_sse_yes = r.status
c.close()
check("MCP SSE stream checks the bearer token", st_sse_no == 401 and st_sse_yes == 200, f"{st_sse_no} {st_sse_yes}")
for label, params in [("serverTransport sse", {"endpointUrl": f"http://127.0.0.1:5678/mcp/tools{RUN}/sse", "serverTransport": "sse"}),
                      ("version 1 sseEndpoint", {"sseEndpoint": f"http://127.0.0.1:5678/mcp/tools{RUN}/sse"})]:
    ex = run_manual(wf([START, node("Agent", LC + "agent", 200, {"promptType": "define", "text": "What is 2+3*4?"}, 1.7), MODEL,
                        node("MCP", LC + "mcpClientTool", 400, params, 1)],
                       ["Start", "Agent"], ai("Model", "Agent", "ai_languageModel"), ai("MCP", "Agent", "ai_tool")))
    check(f"MCP Client Tool over SSE ({label})", ex["status"] == "success" and out(ex, "Agent")[0]["output"] == "Result: 14", f"{ex['data']['resultData'].get('error')}")

print(f"\nall {L.passed} AI cluster checks passed")
