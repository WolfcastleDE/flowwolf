//! Python for the Code node (cargo feature "python"), running on the embedded RustPython VM.
//!
//! On Linux every run happens in a child process (`flowwolf python-sandbox`) with an empty
//! environment, memory/CPU limits and a seccomp filter installed after the interpreter has
//! started: opening files, sockets, exec, fork and kill fail with EPERM, so language-level
//! tricks (`__subclasses__`, `_io.FileIO`, `os.system`) hit the kernel instead of the host.
//! Elsewhere `run` executes in-process without isolation and the caller limits it to admins.

use anyhow::{Result, anyhow};
use serde_json::Value;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

const PRELUDE: &str = r#"
import json
_fw_logs = []
def print(*a, sep=" ", end="", **k):
    _fw_logs.append(sep.join(str(x) for x in a))
class _D(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)
    def __setattr__(self, k, v):
        self[k] = v
    def __delattr__(self, k):
        del self[k]
def _w(v):
    if isinstance(v, dict):
        return _D({k: _w(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_w(x) for x in v]
    return v
_fw_items = _w(json.loads(_fw_in))
_fw_idx = 0
class _Input:
    def all(self):
        return _fw_items
    def first(self):
        return _fw_items[0] if _fw_items else None
    def last(self):
        return _fw_items[-1] if _fw_items else None
    @property
    def item(self):
        return _fw_items[_fw_idx] if _fw_items else None
_input = _Input()
items = _fw_items
def _fw_run(each):
    global _fw_out, _fw_idx
    if each:
        res = []
        for i, it in enumerate(_fw_items):
            _fw_idx = i
            r = _fw_main(it.get("json", _D()), it)
            if r is not None:
                res.extend(r if isinstance(r, list) else [r])
    else:
        res = _fw_main()
    _fw_out = json.dumps({"v": res, "logs": _fw_logs}, default=str)
"#;

/// Runs Code node source in-process. Returns (result value, printed lines).
pub fn run(code: &str, items_json: &str, each: bool, timeout_ms: i64) -> Result<(Value, Vec<String>)> {
    run_inner(code, items_json, each, timeout_ms, false)
}

fn run_inner(code: &str, items_json: &str, each: bool, timeout_ms: i64, lock: bool) -> Result<(Value, Vec<String>)> {
    let (tx, rx) = rustpython_vm::signal::user_signal_channel();
    let interp = rustpython::InterpreterConfig::new()
        .init_stdlib()
        .init_hook(Box::new(move |vm| vm.set_user_signal_channel(rx)))
        .interpreter();
    // Watchdog: raises an exception inside the interpreter when the time is up.
    let done = Arc::new(AtomicBool::new(false));
    let d = done.clone();
    let limit = Duration::from_millis(timeout_ms.max(100) as u64);
    std::thread::spawn(move || {
        let start = Instant::now();
        while !d.load(Ordering::Relaxed) {
            if start.elapsed() > limit {
                let _ = tx.send(Box::new(|vm| Err(vm.new_runtime_error("Code execution timed out".to_owned()))));
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    });
    let body: String = code.lines().map(|l| format!("    {l}\n")).collect();
    let src = format!("{PRELUDE}\ndef _fw_main(_json=None, _item=None):\n{body}    return None\n_fw_run({})\n", if each { "True" } else { "False" });
    let out = interp.enter(|vm| -> Result<String> {
        let scope = vm.new_scope_with_builtins();
        if lock {
            // Load the frozen modules people use most, then close the door before user code runs.
            let warm = "import json, math, random, re, datetime, collections, itertools, functools, string, base64, hashlib";
            vm.run_code_string(vm.new_scope_with_builtins(), warm, "<warmup>".to_owned()).map_err(|_| anyhow!("Python warmup failed"))?;
            lock_down()?;
        }
        scope.globals.set_item("_fw_in", vm.ctx.new_str(items_json).into(), vm).map_err(|_| anyhow!("cannot pass input"))?;
        if let Err(e) = vm.run_code_string(scope.clone(), &src, "<code>".to_owned()) {
            let mut s = String::new();
            let _ = vm.write_exception(&mut s, &e);
            // The last traceback line holds "ErrorType: message".
            return Err(anyhow!("{}", s.lines().last().unwrap_or("Python error").trim()));
        }
        let v = scope.globals.get_item("_fw_out", vm).map_err(|_| anyhow!("no result"))?;
        Ok(v.str(vm).map_err(|_| anyhow!("no result"))?.as_str().to_owned())
    });
    done.store(true, Ordering::Relaxed);
    let v: Value = serde_json::from_str(&out?)?;
    let logs = v["logs"].as_array().into_iter().flatten().map(crate::model::as_str).collect();
    Ok((v["v"].clone(), logs))
}

const MARK: &str = "\n@@flowwolf-result@@";

/// Parent side: runs the code in an isolated child process and enforces the timeout.
pub fn run_isolated(code: &str, items_json: &str, each: bool, timeout_ms: i64) -> Result<(Value, Vec<String>)> {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};
    let mut child = Command::new(std::env::current_exe()?)
        .arg("python-sandbox")
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let req = serde_json::to_vec(&serde_json::json!({"code": code, "items": items_json, "each": each, "timeout": timeout_ms}))?;
    let mut stdin = child.stdin.take().ok_or_else(|| anyhow!("no stdin"))?;
    let writer = std::thread::spawn(move || stdin.write_all(&req));
    let mut stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    // The child stops itself at `timeout`; the extra time covers interpreter start-up.
    let deadline = Instant::now() + Duration::from_millis(timeout_ms.max(100) as u64 + 5000);
    while child.try_wait()?.is_none() {
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(anyhow!("Code execution timed out"));
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    let _ = writer.join();
    let out = reader.join().unwrap_or_default();
    // Only the last marker counts, so text the code writes to stdout cannot fake a result.
    let json = out.rsplit_once(MARK).map(|(_, j)| j).ok_or_else(|| anyhow!("The Python process ended unexpectedly (crash or memory limit)"))?;
    let v: Value = serde_json::from_str(json.trim())?;
    if let Some(e) = v["error"].as_str() {
        return Err(anyhow!("{e}"));
    }
    let logs = v["logs"].as_array().into_iter().flatten().map(crate::model::as_str).collect();
    Ok((v["v"].clone(), logs))
}

/// Child side (`flowwolf python-sandbox`): reads the request from stdin, prints the result.
pub fn sandbox_main() -> ! {
    use std::io::Read;
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    let res = (|| -> Result<(Value, Vec<String>)> {
        let r: Value = serde_json::from_str(&input)?;
        limits();
        let s = |k: &str| r[k].as_str().unwrap_or_default().to_string();
        run_inner(&s("code"), &s("items"), r["each"] == true, r["timeout"].as_i64().unwrap_or(5000), true)
    })();
    let out = match res {
        Ok((v, logs)) => serde_json::json!({"v": v, "logs": logs}),
        Err(e) => serde_json::json!({"error": e.to_string()}),
    };
    println!("{MARK}{out}");
    std::process::exit(0)
}

/// Memory, CPU and core-dump limits for the runner process.
#[cfg(target_os = "linux")]
fn limits() {
    let set = |res, v: u64| unsafe {
        libc::setrlimit(res, &libc::rlimit { rlim_cur: v, rlim_max: v });
    };
    let mb: u64 = std::env::var("N8N_RUNNERS_MAX_MEMORY_MB").ok().and_then(|v| v.parse().ok()).unwrap_or(1024);
    set(libc::RLIMIT_AS, mb << 20);
    set(libc::RLIMIT_CPU, 120);
    set(libc::RLIMIT_CORE, 0);
}

#[cfg(not(target_os = "linux"))]
fn limits() {}

/// Installs a seccomp filter that denies file access, networking, exec, fork and signals to other processes.
#[cfg(target_os = "linux")]
fn lock_down() -> Result<()> {
    use seccompiler::{BpfProgram, SeccompAction, SeccompCmpArgLen, SeccompCmpOp, SeccompCondition, SeccompFilter, SeccompRule};
    #[allow(unused_mut)]
    let mut deny = vec![
        libc::SYS_openat, libc::SYS_openat2, libc::SYS_open_by_handle_at, libc::SYS_name_to_handle_at, libc::SYS_unlinkat, libc::SYS_renameat,
        libc::SYS_renameat2, libc::SYS_mkdirat, libc::SYS_linkat, libc::SYS_symlinkat, libc::SYS_fchmodat, libc::SYS_fchownat, libc::SYS_truncate,
        libc::SYS_socket, libc::SYS_socketpair, libc::SYS_connect, libc::SYS_bind, libc::SYS_listen, libc::SYS_accept, libc::SYS_accept4,
        libc::SYS_execve, libc::SYS_execveat, libc::SYS_kill, libc::SYS_ptrace, libc::SYS_process_vm_readv, libc::SYS_process_vm_writev,
        libc::SYS_io_uring_setup, libc::SYS_mount, libc::SYS_umount2, libc::SYS_chroot, libc::SYS_setrlimit, libc::SYS_prlimit64,
    ];
    #[cfg(target_arch = "x86_64")]
    deny.extend([
        libc::SYS_open, libc::SYS_creat, libc::SYS_unlink, libc::SYS_rename, libc::SYS_mkdir, libc::SYS_rmdir, libc::SYS_link, libc::SYS_symlink,
        libc::SYS_chmod, libc::SYS_chown, libc::SYS_fork, libc::SYS_vfork,
    ]);
    let arch: seccompiler::TargetArch = std::env::consts::ARCH.try_into().map_err(|e| anyhow!("seccomp: {e:?}"))?;
    let install = |rules, action| -> Result<()> {
        let filter = SeccompFilter::new(rules, SeccompAction::Allow, action, arch).map_err(|e| anyhow!("seccomp: {e}"))?;
        let prog: BpfProgram = filter.try_into().map_err(|e| anyhow!("seccomp: {e:?}"))?;
        seccompiler::apply_filter(&prog).map_err(|e| anyhow!("seccomp: {e}"))
    };
    // clone3 hides its flags in memory; ENOSYS makes libc fall back to clone, which we can inspect.
    install([(libc::SYS_clone3, vec![])].into_iter().collect(), SeccompAction::Errno(libc::ENOSYS as u32))?;
    // clone without CLONE_THREAD creates a process (fork); threads stay allowed.
    let no_thread = SeccompCondition::new(0, SeccompCmpArgLen::Qword, SeccompCmpOp::MaskedEq(libc::CLONE_THREAD as u64), 0).map_err(|e| anyhow!("seccomp: {e}"))?;
    deny.push(libc::SYS_clone);
    let rules = deny
        .into_iter()
        .map(|n| (n, if n == libc::SYS_clone { vec![SeccompRule::new(vec![no_thread.clone()]).unwrap()] } else { vec![] }))
        .collect();
    install(rules, SeccompAction::Errno(libc::EPERM as u32))
}

#[cfg(not(target_os = "linux"))]
fn lock_down() -> Result<()> {
    Err(anyhow!("The Python sandbox needs Linux"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_code_node() {
        let items = r#"[{"json":{"n":2}},{"json":{"n":5}}]"#;
        let (v, logs) = run("print('hi', len(_input.all()))\nreturn [{'json': {'sum': sum(i.json.n for i in _input.all())}}]", items, false, 5000).unwrap();
        assert_eq!(v, json!([{"json": {"sum": 7}}]));
        assert_eq!(logs, vec!["hi 2"]);
        let (v, _) = run("_json.double = _json.n * 2\nreturn _item", items, true, 5000).unwrap();
        assert_eq!(v[1]["json"]["double"], json!(10));
        assert!(run("while True:\n    pass", items, false, 300).unwrap_err().to_string().contains("timed out"));
        assert!(run("raise ValueError('boom')", items, false, 2000).unwrap_err().to_string().contains("boom"));
    }
}
