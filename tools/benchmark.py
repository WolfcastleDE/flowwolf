"""Flowwolf vs. n8n on the same machine: same limits (2 CPUs, 2 GB RAM), same workflows
(Webhook -> Code -> Set, once returning one item and once 1,000 items), same load (hey, 50 concurrent
requests for 20 s after a 5 s warm-up). Measures startup time, idle and loaded memory, image size,
throughput and latency.

Usage (needs Docker; pulls n8nio/n8n:latest and williamyeh/hey):
  docker build -t flowwolf:latest .
  python tools/benchmark.py
"""
import http.cookiejar, json, re, subprocess, sys, time, urllib.error, urllib.request

SYSTEMS = {
    "flowwolf": {"image": "flowwolf:latest", "port": 5701, "inner": 5678},
    "n8n": {"image": "n8nio/n8n:latest", "port": 5702, "inner": 5678},
}
OWNER = {"email": "bench@example.com", "firstName": "Bench", "lastName": "Mark", "password": "Bench-pass-123"}


def sh(cmd):
    """cmd as a list: no shell, so quotes reach the program unchanged."""
    if isinstance(cmd, str):
        import shlex
        cmd = shlex.split(cmd)
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def mem_mb(name):
    s = sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name]).split("/")[0].strip()
    m = re.match(r"([\d.]+)\s*([KMG]i?B)", s)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return round(v / 1024 if u.startswith("K") else v * 1024 if u.startswith("G") else v, 1)


class Client:
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"content-type": "application/json", "browser-id": "bench"})
        try:
            with self.op.open(req, timeout=30) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:300]


def wf(name, path, code):
    N = lambda n, t, x, p, v=1: {"id": f"{n}-id", "name": n, "type": t, "typeVersion": v, "position": [x, 0], "parameters": p}
    return {"name": name, "settings": {"executionOrder": "v1"}, "nodes": [
        N("Webhook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": path, "responseMode": "lastNode", "options": {}}, 2),
        N("Code", "n8n-nodes-base.code", 220, {"jsCode": code}, 2),
        N("Set", "n8n-nodes-base.set", 440, {"mode": "manual", "assignments": {"assignments": [{"id": "a", "name": "done", "value": True, "type": "boolean"}]}, "options": {}}, 3.4)],
        "connections": {"Webhook": {"main": [[{"node": "Code", "type": "main", "index": 0}]]}, "Code": {"main": [[{"node": "Set", "type": "main", "index": 0}]]}}}


SIMPLE = "return [{json: {hello: $json.body.name, upper: String($json.body.name).toUpperCase()}}];"
HEAVY = "const out = []; for (let i = 0; i < 1000; i++) out.push({json: {i, sq: i * i, s: 'item-' + i}}); return out;"


def activate(c, wid, version):
    st, j = c.call("POST", f"/rest/workflows/{wid}/activate", {"versionId": version})
    if st < 300:
        return True
    st2, j2 = c.call("PATCH", f"/rest/workflows/{wid}", {"active": True, "versionId": version})
    return st2 < 300 or (print("activate failed", st, j, st2, j2) and False)


def hey(port, path, seconds=20, conc=50):
    out = sh(["docker", "run", "--rm", "williamyeh/hey", "-z", f"{seconds}s", "-c", str(conc), "-m", "POST", "-T", "application/json",
              "-d", '{"name":"bench"}', f"http://host.docker.internal:{port}/webhook/{path}"])
    g = lambda pat: (re.search(pat, out) or [None, None])[1]
    codes = re.findall(r"\[(\d{3})\]\s+(\d+) responses", out)
    return {"rps": float(g(r"Requests/sec:\s+([\d.]+)") or 0), "p50_ms": float(g(r"50% in ([\d.]+) secs") or 0) * 1000,
            "p99_ms": float(g(r"99% in ([\d.]+) secs") or 0) * 1000, "codes": dict(codes)}


results = {}
for name, s in SYSTEMS.items():
    c_name = f"bench-{name}"
    sh(f"docker rm -f {c_name}")
    env = "-e N8N_DIAGNOSTICS_ENABLED=false -e N8N_PERSONALIZATION_ENABLED=false -e N8N_SECURE_COOKIE=false -e EXECUTIONS_DATA_SAVE_ON_SUCCESS=all" if name == "n8n" else ""
    t0 = time.time()
    sh(f"docker run -d --name {c_name} --cpus=2 --memory=2g -p {s['port']}:{s['inner']} {env} {s['image']}")
    base = f"http://localhost:{s['port']}"
    while time.time() - t0 < 180:
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(0.2)
    startup = round(time.time() - t0, 2)
    time.sleep(15)
    idle = mem_mb(c_name)
    c = Client(base)
    st, j = c.call("POST", "/rest/owner/setup", OWNER)
    if st >= 300:
        print(name, "setup", st, j); sys.exit(1)
    r = {"startup_s": startup, "idle_mb": idle, "image": sh(["docker", "images", "--format", "{{.Size}}", s["image"]])}
    for label, code in (("simple", SIMPLE), ("1000_items", HEAVY)):
        path = f"bench-{label}"
        st, j = c.call("POST", "/rest/workflows", wf(f"Bench {label}", path, code))
        w = j["data"] if isinstance(j, dict) and "data" in j else j
        if st >= 300 or not activate(c, w["id"], w.get("versionId")):
            print(name, "workflow", st, str(j)[:300]); sys.exit(1)
        time.sleep(2)
        probe = urllib.request.urlopen(urllib.request.Request(f"{base}/webhook/{path}", data=b'{"name":"bench"}', headers={"content-type": "application/json"}), timeout=30).read()[:120]
        hey(s["port"], path, seconds=5)  # warm-up
        res = hey(s["port"], path)
        res["mem_after_mb"] = mem_mb(c_name)
        res["probe"] = probe.decode(errors="replace")
        r[label] = res
    results[name] = r
    print(name, json.dumps(r))
    sh(f"docker rm -f {c_name}")

print("RESULT " + json.dumps(results))
