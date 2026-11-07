"""Storage nodes against real servers: AWS S3 against SeaweedFS (S3 API, Signature V4 checked) and MongoDB.

Usage: python tests/e2e_storage.py [base_url] [service_host]
  service_host: where an S3 server (8333, minioadmin/minioadmin; SeaweedFS with tests/s3.json) and MongoDB
  (27018, root / "pw ä&?") listen, as seen from the container.
"""

import base64
import json
import sys
import time

import e2e_lib as L

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
HOST = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
rest, node, chain, out, check, run_manual = L.rest, L.node, L.chain, L.out, L.check, L.run_manual
L.login_or_setup()
RUN = int(time.time())

creds = {}


def cred(kind, data):
    key = (kind, json.dumps(data, sort_keys=True))
    if key not in creds:
        creds[key] = rest("POST", "/credentials", {"name": f"{kind} {len(creds)}", "type": kind, "data": data})["id"]
    return {kind: {"id": creds[key], "name": kind}}


def run(kind, cred_kind, cred_data, params, items=None):
    nodes = [node("Start", "n8n-nodes-base.manualTrigger", 0)]
    names = ["Start"]
    if items is not None:
        nodes.append(node("Items", "n8n-nodes-base.code", 100, {"jsCode": f"return {json.dumps(items)};"}, 2))
        names.append("Items")
    nodes.append(node("N", kind, 200, params, credentials=cred(cred_kind, cred_data)))
    ex = run_manual({"name": "storage", "nodes": nodes, "connections": chain(*names, "N")})
    err = ex["data"]["resultData"].get("error")
    items_out = ex["data"]["resultData"]["runData"]["N"][-1]["data"]["main"][0] if ex["status"] == "success" else []
    return ex["status"], items_out, err


def binary(name, data, mime):
    return [{"json": {"n": name}, "binary": {"data": {"data": base64.b64encode(data).decode(), "mimeType": mime, "fileName": name}}}]


# ---------------------------------------------------------------- AWS S3 against SeaweedFS
S3 = ("n8n-nodes-base.awsS3", "aws", {"region": "us-east-1", "accessKeyId": "minioadmin", "secretAccessKey": "minioadmin",
                                      "customEndpoints": True, "s3Endpoint": f"http://{HOST}:8333"})
B = f"fw-{RUN}"
st, o, err = run(*S3, {"resource": "bucket", "operation": "create", "name": B})
check("S3: create bucket", st == "success" and o[0]["json"] == {"success": True}, err)
st, o, err = run(*S3, {"resource": "bucket", "operation": "getAll", "returnAll": True})
check("S3: list buckets", st == "success" and B in [x["json"]["Name"] for x in o], f"{err} {o}")
st, o, err = run(*S3, {"resource": "file", "operation": "upload", "bucketName": B, "fileName": "Bericht März.txt", "binaryPropertyName": "data",
                       "additionalFields": {"parentFolderKey": "reports"}, "tagsUi": {"tagsValues": [{"key": "team", "value": "ops"}]}},
                 items=binary("x.txt", "Grüße aus Flowwolf".encode(), "text/plain"))
check("S3: upload binary into a folder (umlauts and spaces in the key, tags)", st == "success", err)
st, o, err = run(*S3, {"resource": "file", "operation": "upload", "bucketName": B, "fileName": "notes.md", "binaryData": False, "fileContent": "# Notes"})
check("S3: upload text content", st == "success", err)
st, o, err = run(*S3, {"resource": "folder", "operation": "create", "bucketName": B, "folderName": "archive"})
check("S3: create folder", st == "success", err)
st, o, err = run(*S3, {"resource": "file", "operation": "getAll", "bucketName": B, "returnAll": True})
keys = sorted(x["json"]["Key"] for x in o)
check("S3: list files leaves folders out", st == "success" and keys == ["notes.md", "reports/Bericht März.txt"], f"{err} {keys}")
st, o, err = run(*S3, {"resource": "folder", "operation": "getAll", "bucketName": B, "returnAll": True})
check("S3: list folders", st == "success" and [x["json"]["Key"] for x in o] == ["archive/"], f"{err} {o}")
st, o, err = run(*S3, {"resource": "file", "operation": "getAll", "bucketName": B, "options": {"folderKey": "reports"}, "returnAll": True})
check("S3: list files in a folder", st == "success" and [x["json"]["Key"] for x in o] == ["reports/Bericht März.txt"], f"{err} {o}")
st, o, err = run(*S3, {"resource": "file", "operation": "download", "bucketName": B, "fileKey": "reports/Bericht März.txt", "binaryPropertyName": "data"})
b = o[0].get("binary", {}).get("data", {}) if o else {}
check("S3: download into binary data", st == "success" and base64.b64decode(b.get("data", "")) == "Grüße aus Flowwolf".encode()
      and b.get("fileName") == "Bericht März.txt" and b.get("mimeType") == "text/plain", f"{err} {b}")
st, o, err = run(*S3, {"resource": "file", "operation": "copy", "sourcePath": f"/{B}/reports/Bericht März.txt", "destinationPath": f"/{B}/archive/copy.txt"})
check("S3: copy a file", st == "success" and "ETag" in o[0]["json"], f"{err} {o}")
st, o, err = run(*S3, {"resource": "bucket", "operation": "search", "bucketName": B, "additionalFields": {"prefix": "archive/"}, "returnAll": True})
check("S3: search a bucket by prefix", st == "success" and sorted(x["json"]["Key"] for x in o) == ["archive/", "archive/copy.txt"], f"{err} {o}")
st, o, err = run(*S3, {"resource": "file", "operation": "getAll", "bucketName": B, "limit": 1})
check("S3: limit", st == "success" and len(o) == 1, f"{err} {o}")
st, o, err = run(*S3, {"resource": "file", "operation": "delete", "bucketName": B, "fileKey": "notes.md"})
check("S3: delete a file", st == "success", err)
st, o, err = run(*S3, {"resource": "folder", "operation": "delete", "bucketName": B, "folderKey": "archive"})
check("S3: delete a folder with its files (DeleteObjects)", st == "success", err)
st, o, err = run(*S3, {"resource": "bucket", "operation": "search", "bucketName": B, "returnAll": True})
check("S3: only the untouched file is left", st == "success" and [x["json"]["Key"] for x in o] == ["reports/Bericht März.txt"], f"{err} {o}")
st, o, err = run(*S3, {"resource": "bucket", "operation": "delete", "name": B})
check("S3: a bucket with files cannot be deleted (S3 error is passed on)", st == "error" and "BucketNotEmpty" in str(err), str(err))
run(*S3, {"resource": "file", "operation": "delete", "bucketName": B, "fileKey": "reports/Bericht März.txt"})
st, o, err = run(*S3, {"resource": "bucket", "operation": "delete", "name": B})
check("S3: delete the empty bucket", st == "success", err)

# ---------------------------------------------------------------- MongoDB against mongo:7
MG = ("n8n-nodes-base.mongoDb", "mongoDb", {"configurationType": "values", "host": HOST, "port": 27018, "user": "root", "password": "pw ä&?", "database": f"fw{RUN}"})
people = [{"json": {"name": "Ada", "city": "Berlin", "age": 36, "joined": "2026-01-02T03:04:05Z"}}, {"json": {"name": "Linus", "city": "Hamburg", "age": 28, "joined": "2026-02-01T00:00:00Z"}}]
st, o, err = run(*MG, {"operation": "insert", "collection": "people", "fields": "name,city,age,joined", "options": {"dateFields": "joined"}}, items=people)
check("MongoDB: insert items (password with special characters, date fields)", st == "success" and len(o) == 2 and len(o[0]["json"]["id"]) == 24, f"{err} {o}")
st, o, err = run(*MG, {"operation": "find", "collection": "people", "query": '{"age": {"$gt": 30}}'})
check("MongoDB: find with a query (ObjectId and date as strings)", st == "success" and [x["json"]["name"] for x in o] == ["Ada"]
      and len(o[0]["json"]["_id"]) == 24 and o[0]["json"]["joined"].startswith("2026-01-02T03:04:05"), f"{err} {o}")
ada_id = o[0]["json"]["_id"] if o else ""
st, o, err = run(*MG, {"operation": "find", "collection": "people", "query": "{}", "options": {"sort": '{"age": 1}', "limit": 1, "projection": '{"_id": 0, "name": 1}'}})
check("MongoDB: find with sort, limit and projection", st == "success" and [x["json"] for x in o] == [{"name": "Linus"}], f"{err} {o}")
st, o, err = run(*MG, {"operation": "update", "collection": "people", "updateKey": "_id", "fields": "city"}, items=[{"json": {"_id": ada_id, "city": "München"}}])
st2, o2, err2 = run(*MG, {"operation": "find", "collection": "people", "query": json.dumps({"_id": ada_id})})
check("MongoDB: update by _id (hex string becomes ObjectId)", st == "success" and st2 == "success" and o2[0]["json"]["city"] == "München", f"{err} {err2} {o2}")
st, o, err = run(*MG, {"operation": "update", "collection": "people", "updateKey": "name", "fields": "name,city", "upsert": True},
                 items=[{"json": {"name": "Grace", "city": "Köln"}}])
st2, o2, err2 = run(*MG, {"operation": "find", "collection": "people", "query": '{"name": "Grace"}'})
check("MongoDB: update with upsert inserts a missing document", st == "success" and len(o2) == 1 and o2[0]["json"]["city"] == "Köln", f"{err} {err2} {o2}")
st, o, err = run(*MG, {"operation": "findOneAndReplace", "collection": "people", "updateKey": "name", "fields": "name,city"},
                 items=[{"json": {"name": "Linus", "city": "Helsinki"}}])
st2, o2, err2 = run(*MG, {"operation": "find", "collection": "people", "query": '{"name": "Linus"}'})
check("MongoDB: find and replace swaps the whole document", st == "success" and o2[0]["json"].get("city") == "Helsinki" and "age" not in o2[0]["json"], f"{err} {o2}")
st, o, err = run(*MG, {"operation": "aggregate", "collection": "people", "query": '[{"$group": {"_id": null, "n": {"$sum": 1}}}]'})
check("MongoDB: aggregate pipeline", st == "success" and o[0]["json"]["n"] == 3, f"{err} {o}")
st, o, err = run(*MG, {"operation": "delete", "collection": "people", "query": '{"city": {"$in": ["Köln", "Helsinki"]}}'})
check("MongoDB: delete many", st == "success" and o[0]["json"] == {"deletedCount": 2}, f"{err} {o}")
st, o, err = run(*MG, {"operation": "find", "collection": "people", "query": "{bad json"})
check("MongoDB: invalid JSON in the query fails clearly", st == "error" and "valid JSON" in str(err), str(err))

# ---------------------------------------------------------------- FTP and SFTP against real servers
def ftp_suite(label, cred_kind, cred_data, protocol, root):
    FT = ("n8n-nodes-base.ftp", cred_kind, cred_data)
    base = f"{root}/fw{RUN}"
    p = lambda **kw: {"protocol": protocol, **kw}  # noqa: E731
    st, o, err = run(*FT, p(operation="upload", path=f"{base}/docs/Bericht März.txt", binaryPropertyName="data"), items=binary("x.txt", "Grüße per FTP".encode(), "text/plain"))
    check(f"{label}: upload binary (missing folders are created)", st == "success" and o[0]["json"]["success"] is True, err)
    st, o, err = run(*FT, p(operation="upload", path=f"{base}/notes.md", binaryData=False, fileContent="# Notes"))
    check(f"{label}: upload text content", st == "success", err)
    st, o, err = run(*FT, p(operation="list", path=base))
    check(f"{label}: list a folder", st == "success" and sorted((x["json"]["type"], x["json"]["name"]) for x in o) == [("-", "notes.md"), ("d", "docs")], f"{err} {o}")
    st, o, err = run(*FT, p(operation="list", path=base, recursive=True))
    check(f"{label}: list recursively", st == "success" and sorted(x["json"]["path"] for x in o) == sorted([f"{base}/docs", f"{base}/notes.md", f"{base}/docs/Bericht März.txt"]),
          f"{err} {[x['json']['path'] for x in o]}")
    st, o, err = run(*FT, p(operation="download", path=f"{base}/docs/Bericht März.txt", binaryPropertyName="data"))
    b = o[0].get("binary", {}).get("data", {}) if o else {}
    check(f"{label}: download into binary data", st == "success" and base64.b64decode(b.get("data", "")) == "Grüße per FTP".encode() and b.get("fileName") == "Bericht März.txt",
          f"{err} {b}")
    st, o, err = run(*FT, p(operation="rename", oldPath=f"{base}/notes.md", newPath=f"{base}/archive/2026/notes.md", options={"createDirectories": True}))
    check(f"{label}: rename into new folders", st == "success", err)
    st, o, err = run(*FT, p(operation="delete", path=f"{base}/archive/2026/notes.md"))
    check(f"{label}: delete a file", st == "success", err)
    st, o, err = run(*FT, p(operation="download", path=f"{base}/missing.txt"))
    check(f"{label}: a missing file fails clearly", st == "error" and "missing.txt" in str(err), str(err))
    st, o, err = run(*FT, p(operation="delete", path=base, options={"folder": True, "recursive": True}))
    st2, o2, err2 = run(*FT, p(operation="list", path=root))
    check(f"{label}: delete a folder recursively", st == "success" and f"fw{RUN}" not in [x["json"]["name"] for x in o2], f"{err} {err2}")


ftp_suite("FTP", "ftp", {"host": HOST, "port": 2121, "username": "fw", "password": "pw"}, "ftp", "/ftp/fw")
ftp_suite("SFTP", "sftp", {"host": HOST, "port": 2222, "username": "fw", "password": "pw"}, "sftp", "/upload")

print(f"\nall {L.passed} storage checks passed")
