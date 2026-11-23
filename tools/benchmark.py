"""Flowwolf vs. n8n on the same machine: same limits (2 CPUs, 2 GB RAM), same workflows, same load
(hey for 20 s after a 5 s warm-up). Workflows: Webhook -> Code -> Set returning one item and 1,000 items
(50 concurrent requests), and Webhook -> HTTP Request -> Set against a backend that answers after 500 ms
(50 and 200 concurrent requests), which shows how both cope with many executions waiting at once.
Measures startup time, idle and loaded memory, image size, throughput and p50/p95/p99 latency.
Everything runs on one Docker network, so hey and the backend reach the systems without the host in between.

With --queue both run in queue mode on PostgreSQL: a main process and two workers with 25 slots each, every
container limited to 1 CPU and 1 GB (n8n additionally uses Redis). Memory is the sum of main and workers.

Usage (needs Docker; pulls n8nio/n8n:latest, williamyeh/hey, python, postgres and redis):
  docker build -t flowwolf:latest .
  python tools/benchmark.py [--queue]
"""
import http.cookiejar, json, re, subprocess, sys, time, urllib.error, urllib.request

SYSTEMS = {
    "flowwolf": {"image": "flowwolf:latest", "port": 5701, "inner": 5678},
    "n8n": {"image": "n8nio/n8n:latest", "port": 5702, "inner": 5678},
}
NET, SLOW_DELAY = "bench-net", 0.5
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


def wf(name, path, middle):
    N = lambda n, t, x, p, v=1: {"id": f"{n}-id", "name": n, "type": t, "typeVersion": v, "position": [x, 0], "parameters": p}
    mid = (N("Code", "n8n-nodes-base.code", 220, {"jsCode": middle}, 2) if isinstance(middle, str)
           else N("Code", "n8n-nodes-base.httpRequest", 220, {"url": middle["url"], "options": {}}, 4.2))
    return {"name": name, "settings": {"executionOrder": "v1"}, "nodes": [
        N("Webhook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": path, "responseMode": "lastNode", "options": {}}, 2),
        mid,
        N("Set", "n8n-nodes-base.set", 440, {"mode": "manual", "assignments": {"assignments": [{"id": "a", "name": "done", "value": True, "type": "boolean"}]}, "options": {}}, 3.4)],
        "connections": {"Webhook": {"main": [[{"node": "Code", "type": "main", "index": 0}]]}, "Code": {"main": [[{"node": "Set", "type": "main", "index": 0}]]}}}


SIMPLE = "return [{json: {hello: $json.body.name, upper: String($json.body.name).toUpperCase()}}];"
SLOW = {"url": "http://bench-slow:8000/"}
HEAVY = "const out = []; for (let i = 0; i < 1000; i++) out.push({json: {i, sq: i * i, s: 'item-' + i}}); return out;"


def activate(c, wid, version):
    st, j = c.call("POST", f"/rest/workflows/{wid}/activate", {"versionId": version})
    if st < 300:
        return True
    st2, j2 = c.call("PATCH", f"/rest/workflows/{wid}", {"active": True, "versionId": version})
    return st2 < 300 or (print("activate failed", st, j, st2, j2) and False)


def hey(url, seconds=20, conc=50):
    out = sh(["docker", "run", "--rm", "--network", NET, "williamyeh/hey", "-z", f"{seconds}s", "-c", str(conc), "-t", "120",
              "-m", "POST", "-T", "application/json", "-d", '{"name":"bench"}', url])
    g = lambda pat: (re.search(pat, out) or [None, None])[1]
    codes = {k: int(v) for k, v in re.findall(r"\[(\d{3})\]\s+(\d+) responses", out)}
    ok = codes.get("200", 0)
    pct = {f"p{q}_ms": round(float(g(rf"{q}% in ([\d.]+) secs") or 0) * 1000) for q in (50, 95, 99)}
    # Only answered requests count: timeouts and errors are not throughput.
    return {"ok_per_s": round(ok / seconds, 1), **pct, "codes": codes}


sh(f"docker network create {NET}")
SLOW_SERVER = f"""import http.server, time
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep({SLOW_DELAY})
        b = b'{{"slow": true}}'
        self.send_response(200); self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer.request_queue_size = 1024
http.server.ThreadingHTTPServer(('', 8000), H).serve_forever()
"""
sh("docker rm -f bench-slow")
sh(["docker", "run", "-d", "--name", "bench-slow", "--network", NET, "python:3-alpine", "python", "-c", SLOW_SERVER])
time.sleep(3)
# Calibration: the backend alone must keep up, otherwise it, not the system under test, is the limit.
out = sh(["docker", "run", "--rm", "--network", NET, "williamyeh/hey", "-z", "10s", "-c", "200", "http://bench-slow:8000/"])
print("backend alone (200 concurrent):", re.search(r"Requests/sec:\s+([\d.]+)", out)[1], "req/s,",
      "p95", re.search(r"95% in ([\d.]+) secs", out)[1], "s")

# --queue: each system on PostgreSQL with a main process and WORKERS workers (n8n also needs Redis).
# Main and each worker get 1 CPU and 1 GB; database and Redis are infrastructure and stay unlimited.
QUEUE = "--queue" in sys.argv
WORKERS, WORKER_SLOTS = 2, 25
PG = ["-e", "DB_TYPE=postgresdb", "-e", "DB_POSTGRESDB_HOST=bench-pg", "-e", "DB_POSTGRESDB_DATABASE=bench",
      "-e", "DB_POSTGRESDB_USER=bench", "-e", "DB_POSTGRESDB_PASSWORD=bench"]
N8N_ENV = ["-e", "N8N_DIAGNOSTICS_ENABLED=false", "-e", "N8N_PERSONALIZATION_ENABLED=false", "-e", "N8N_SECURE_COOKIE=false",
           "-e", "EXECUTIONS_DATA_SAVE_ON_SUCCESS=all"]


def start(name, s):
    """Starts the system and returns the names of its app containers (main first)."""
    main = f"bench-{name}"
    if not QUEUE:
        sh(["docker", "run", "-d", "--name", main, "--network", NET, "--cpus=2", "--memory=2g", "-p", f"{s['port']}:{s['inner']}",
            *(N8N_ENV if name == "n8n" else []), s["image"]])
        return [main]
    sh(["docker", "run", "-d", "--name", "bench-pg", "--network", NET, "-e", "POSTGRES_USER=bench", "-e", "POSTGRES_PASSWORD=bench",
        "-e", "POSTGRES_DB=bench", "postgres:16-alpine"])
    if name == "n8n":
        sh(["docker", "run", "-d", "--name", "bench-redis", "--network", NET, "redis:7-alpine"])
    for _ in range(60):
        if "accepting" in sh(["docker", "exec", "bench-pg", "pg_isready", "-U", "bench"]):
            break
        time.sleep(0.5)
    time.sleep(2)
    env = PG + ["-e", "EXECUTIONS_MODE=queue", "-e", "N8N_ENCRYPTION_KEY=bench-key", "-e", "FLOWWOLF_ENCRYPTION_KEY=bench-key"]
    if name == "n8n":
        env += N8N_ENV + ["-e", "QUEUE_BULL_REDIS_HOST=bench-redis"]
    else:
        env += ["-e", f"FLOWWOLF_WORKER_CONCURRENCY={WORKER_SLOTS}"]
    lim = ["--cpus=1", "--memory=1g", "--network", NET]
    sh(["docker", "run", "-d", "--name", main, *lim, "-p", f"{s['port']}:{s['inner']}", *env, s["image"]])
    workers = []
    for i in range(1, WORKERS + 1):
        w = f"{main}-w{i}"
        args = ["worker", f"--concurrency={WORKER_SLOTS}"] if name == "n8n" else ["worker"]
        sh(["docker", "run", "-d", "--name", w, "--hostname", w, *lim, *env, s["image"], *args])
        workers.append(w)
    return [main, *workers]


def stop(names):
    sh(["docker", "rm", "-f", *names, "bench-pg", "bench-redis"])


RUNS = ((("simple", SIMPLE, 50), ("slow_backend_50", SLOW, 50), ("slow_backend_200", SLOW, 200)) if QUEUE else
        (("simple", SIMPLE, 50), ("1000_items", HEAVY, 50), ("slow_backend_50", SLOW, 50), ("slow_backend_200", SLOW, 200)))
results = {}
for name, s in SYSTEMS.items():
    c_name = f"bench-{name}"
    stop([c_name, f"{c_name}-w1", f"{c_name}-w2"])
    t0 = time.time()
    names = start(name, s)
    base = f"http://localhost:{s['port']}"
    while time.time() - t0 < 180:
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(0.2)
    startup = round(time.time() - t0, 2)
    time.sleep(15)
    mem = lambda: round(sum(mem_mb(n) or 0 for n in names), 1)
    idle = mem()
    c = Client(base)
    for _ in range(120):  # n8n on PostgreSQL answers /healthz before its REST routes exist
        st, j = c.call("POST", "/rest/owner/setup", OWNER)
        if st != 404:
            break
        time.sleep(1)
    if st >= 300:
        print(name, "setup", st, j); sys.exit(1)
    r = {"mode": "queue" if QUEUE else "regular", "startup_s": startup, "idle_mb": idle,
         "image": sh(["docker", "images", "--format", "{{.Size}}", s["image"]]),
         "version": sh(["docker", "exec", c_name, "n8n", "--version"]) if name == "n8n" else ""}
    for label, middle, conc in RUNS:
        path = f"bench-{label}"
        st, j = c.call("POST", "/rest/workflows", wf(f"Bench {label}", path, middle))
        w = j["data"] if isinstance(j, dict) and "data" in j else j
        if st >= 300 or not activate(c, w["id"], w.get("versionId")):
            print(name, "workflow", st, str(j)[:300]); sys.exit(1)
        time.sleep(2)
        probe = urllib.request.urlopen(urllib.request.Request(f"{base}/webhook/{path}", data=b'{"name":"bench"}', headers={"content-type": "application/json"}), timeout=30).read()[:120]
        url = f"http://{c_name}:{s['inner']}/webhook/{path}"
        hey(url, seconds=5, conc=conc)  # warm-up
        res = hey(url, conc=conc)
        res["mem_after_mb"] = mem()
        res["probe"] = probe.decode(errors="replace")
        r[label] = res
    results[name] = r
    print(name, json.dumps(r))
    stop(names)

sh("docker rm -f bench-slow")
sh(f"docker network rm {NET}")
print("RESULT " + json.dumps(results))
