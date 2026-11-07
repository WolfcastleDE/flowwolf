"""Email (SMTP send + IMAP trigger against GreenMail) and more core nodes.

Usage: python tests/e2e_mail_core.py [base_url] [mail_host]
GreenMail: SMTP 3025, IMAPS 3993, auth disabled. Fresh Flowwolf instance.
"""

import base64
import sys
import time

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
MAIL = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
call, rest, node, chain, run_manual, out, check = L.call, L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()
INNER = "http://127.0.0.1:5678"


def flow(*nodes):
    return {"name": "t", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), *nodes], "connections": chain("Start", *[n["name"] for n in nodes])}


# ---------------------------------------------------------------- email: send via SMTP, receive via IMAP trigger
smtp = rest("POST", "/credentials", {"name": "smtp", "type": "smtp", "data": {"host": MAIL, "port": 3025, "secure": False, "disableStartTls": True}})
imap = rest("POST", "/credentials", {"name": "imap", "type": "imap", "data": {"host": MAIL, "port": 3993, "secure": True, "allowUnauthorizedCerts": True, "user": "bob@example.com", "password": "x"}})
receiver = rest("POST", "/workflows", {"name": "inbox", "nodes": [
    node("Mail", "n8n-nodes-base.emailReadImap", 0, {"mailbox": "INBOX", "postProcessAction": "read", "format": "simple", "options": {"pollIntervalSeconds": 2}}, 2.1,
         credentials={"imap": {"id": imap["id"], "name": "imap"}}),
    node("Seen", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "got", "type": "string", "value": "={{ $json.subject }} from {{ $json.from }}"}]}}, 3.4)],
    "connections": chain("Mail", "Seen")})
rest("POST", f"/workflows/{receiver['id']}/activate")
ex = run_manual(flow(node("Send", "n8n-nodes-base.emailSend", 200, {"fromEmail": "Ada <ada@example.com>", "toEmail": "bob@example.com", "subject": "={{ 'Report ' + 42 }}",
                                                                     "emailFormat": "both", "text": "Plain body", "html": "<p>HTML body</p>"}, 2.1, credentials={"smtp": {"id": smtp["id"], "name": "smtp"}})))
check("Send Email delivers through SMTP", ex["status"] == "success" and out(ex, "Send")[0]["accepted"] is True, f"{ex['data']['resultData'].get('error')}")
got = None
for _ in range(30):
    runs = rest("GET", f"/executions?workflowId={receiver['id']}")
    if runs:
        got = L.wait_exec(runs[0]["id"])
        break
    time.sleep(1)
check("Email Trigger (IMAPS) starts the workflow for the new mail", got is not None and out(got, "Seen")[0]["got"] == "Report 42 from Ada <ada@example.com>", f"{got and got['data']['resultData']}")
check("mail body is parsed", got is not None and got["data"]["resultData"]["runData"]["Mail"][0]["data"]["main"][0][0]["json"]["textPlain"].strip() == "Plain body")
time.sleep(5)
check("the mail is marked as read and not processed twice", len(rest("GET", f"/executions?workflowId={receiver['id']}")) == 1)
rest("POST", f"/workflows/{receiver['id']}/deactivate")

# ---------------------------------------------------------------- XML, Markdown
ex = run_manual(flow(
    node("Data", "n8n-nodes-base.set", 200, {"mode": "raw", "jsonOutput": '{"order": {"$": {"id": "7"}, "item": [{"$": {"sku": "a"}, "_": "Pen & Ink"}, "Paper"]}}'}, 3.4),
    node("ToXml", "n8n-nodes-base.xml", 400, {"mode": "jsonToXml", "dataPropertyName": "xml", "options": {"headless": True}}, 1),
    node("ToJson", "n8n-nodes-base.xml", 600, {"mode": "xmlToJson", "dataPropertyName": "xml"}, 1)))
check("XML: JSON to XML", out(ex, "ToXml")[0]["xml"] == '<order id="7"><item sku="a">Pen &amp; Ink</item><item>Paper</item></order>', f"{out(ex, 'ToXml')}")
check("XML: back to the same JSON", out(ex, "ToJson")[0]["order"] == {"$": {"id": "7"}, "item": [{"$": {"sku": "a"}, "_": "Pen & Ink"}, "Paper"]})
ex = run_manual(flow(node("Md", "n8n-nodes-base.markdown", 200, {"mode": "markdownToHtml", "markdown": "# Title\n\n- **one**\n- two", "destinationKey": "html"}, 1),
                     node("Back", "n8n-nodes-base.markdown", 400, {"mode": "htmlToMarkdown", "html": "={{ $json.html }}", "destinationKey": "md"}, 1)))
check("Markdown round trip", out(ex, "Back")[0]["md"] == "# Title\n\n- **one**\n- two", f"{out(ex, 'Back')}")

# ---------------------------------------------------------------- Compression
ex = run_manual(flow(
    node("Text", "n8n-nodes-base.set", 200, {"mode": "manual", "assignments": {"assignments": [{"name": "data", "type": "string", "value": "hello zip"}]}}, 3.4),
    node("File", "n8n-nodes-base.convertToFile", 400, {"operation": "toText", "sourceProperty": "data", "options": {"fileName": "note.txt"}}, 1.1),
    node("Zip", "n8n-nodes-base.compression", 600, {"operation": "compress", "binaryPropertyName": "data", "outputFormat": "zip", "fileName": "bundle.zip"}, 1.1),
    node("Unzip", "n8n-nodes-base.compression", 800, {"operation": "decompress", "binaryPropertyName": "data"}, 1.1)))
item = ex["data"]["resultData"]["runData"]["Unzip"][0]["data"]["main"][0][0]
check("zip and unzip a file", ex["status"] == "success" and base64.b64decode(item["binary"]["file_0"]["data"]) == b"hello zip" and item["binary"]["file_0"]["fileName"] == "note.txt", f"{ex['data']['resultData'].get('error')}")

# ---------------------------------------------------------------- Execution Data, Debug Helper
ex = run_manual(flow(node("Meta", "n8n-nodes-base.executionData", 200, {"dataToSave": {"values": [{"key": "orderId", "value": "={{ 1000 + 7 }}"}]}}, 1),
                     node("Gen", "n8n-nodes-base.debugHelper", 400, {"category": "randomData", "randomDataType": "user", "randomDataCount": 5}, 1)))
check("Execution Data is stored with the execution", ex["data"]["customData"] == {"orderId": "1007"}, f"{ex['data'].get('customData')}")
check("Debug Helper generates random users", len(out(ex, "Gen")) == 5 and "@example.com" in out(ex, "Gen")[0]["email"])

# ---------------------------------------------------------------- Workflow Trigger
wt = rest("POST", "/workflows", {"name": "on activate", "nodes": [node("On", "n8n-nodes-base.workflowTrigger", 0, {"events": ["activate", "update"]}, 1), node("NoOp", "n8n-nodes-base.noOp", 200)],
                                 "connections": chain("On", "NoOp")})
rest("POST", f"/workflows/{wt['id']}/activate")
rest("PATCH", f"/workflows/{wt['id']}", {"name": "on activate v2"})
time.sleep(1.5)
events = sorted(out(L.wait_exec(e["id"]), "On")[0]["event"] for e in rest("GET", f"/executions?workflowId={wt['id']}"))
check("Workflow Trigger fires on activation and on update", events == ["Workflow activated", "Workflow updated"], f"{events}")

# ---------------------------------------------------------------- Flowwolf/n8n API node
key = rest("POST", "/api-keys", {"label": "self"})["apiKey"]
api = rest("POST", "/credentials", {"name": "self api", "type": "n8nApi", "data": {"apiKey": key, "baseUrl": INNER}})
ex = run_manual(flow(node("API", "n8n-nodes-base.n8n", 200, {"resource": "workflow", "operation": "getAll"}, 1, credentials={"n8nApi": {"id": api["id"], "name": "self api"}})))
names = {w["name"] for w in out(ex, "API")} if ex["status"] == "success" else ex["data"]["resultData"]["error"]
check("API node lists workflows through the public API", isinstance(names, set) and {"inbox", "on activate v2"} <= names, f"{names}")

print(f"\nall {L.passed} mail & core checks passed")
