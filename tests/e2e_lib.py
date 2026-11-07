"""Shared helpers for the end-to-end scripts."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse  # noqa: F401  (used by the scripts)
import urllib.request

BASE = "http://localhost:5679"
OWNER = {"email": "owner@example.com", "password": "correct horse 42", "firstName": "Ada"}
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
passed = 0


def call(method, path, body=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json", **(headers or {})})
    try:
        with opener.open(req, timeout=60) as r:
            text = r.read().decode()
            return r.status, (text if raw else (json.loads(text) if text else None))
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        if raw:
            return e.code, text
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def rest(method, path, body=None):
    status, j = call(method, "/rest" + path, body)
    assert status < 300, f"{method} {path} -> {status}: {j}"
    return j["data"]


def login_or_setup():
    if not rest("GET", "/settings")["setupComplete"]:
        rest("POST", "/owner/setup", OWNER)
    else:
        rest("POST", "/login", {"email": OWNER["email"], "password": OWNER["password"]})


def node(name, kind, x, params=None, version=1, **extra):
    return {"name": name, "type": kind, "typeVersion": version, "position": [x, 0], "parameters": params or {}, **extra}


def chain(*names):
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(names, names[1:])}


def wait_exec(exec_id):
    for _ in range(300):
        ex = rest("GET", f"/executions/{exec_id}")
        if ex["status"] not in ("running", "waiting", "queued", "canceling"):
            return ex
        time.sleep(0.1)
    raise AssertionError("execution did not finish")


def run_manual(wf, dest=None):
    return wait_exec(rest("POST", "/workflows/run", {"workflowData": wf, "destinationNode": dest})["executionId"])


def out(ex, name, branch=0):
    return [i["json"] for i in ex["data"]["resultData"]["runData"][name][-1]["data"]["main"][branch]]


def check(label, cond, detail=""):
    global passed
    assert cond, f"FAILED: {label} {detail}"
    passed += 1
    print(f"  ok  {label}")
