"""
agent/sandbox.py
=================
Runs an untrusted student binary SAFELY.

What this sandbox guarantees (Linux only):
    * wall-clock timeout   -> the process group is SIGKILLed (infinite loops die)
    * CPU-time limit       -> RLIMIT_CPU (busy loops die even if we are slow)
    * memory limit         -> RLIMIT_AS  (address space cap)
    * output size limit    -> RLIMIT_FSIZE (a program printing forever is cut off)
    * no core dumps, limited number of child processes (fork-bomb protection)
    * runs in its own temp directory, stdout/stderr captured into files

What it does NOT do (be honest in your report!):
    It is NOT a full jail: the program can still read world-readable files and
    open network sockets.  For production use wrap this in Docker / nsjail /
    bubblewrap.  See the README "Security note".

HOW limits are applied:
    We prefer the `prlimit` command from util-linux (installed on Ubuntu).  It
    sets the limits and then exec()s the student program.  This avoids
    `preexec_fn`, which is unsafe when Python uses threads (our coordinator runs
    analyzers in a thread pool).  If `prlimit` is missing we fall back to
    `preexec_fn` + resource.setrlimit.
"""
from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

MAX_CAPTURE_BYTES = 64 * 1024        # we only keep the first 64 KB of output
OUTPUT_FILE_LIMIT = 1024 * 1024      # RLIMIT_FSIZE: kill programs that print > 1 MB


@dataclass
class RunResult:
    """Outcome of one sandboxed execution."""
    status: str            # OK | TIMEOUT | RUNTIME_ERROR | MEMORY_LIMIT | OUTPUT_LIMIT
    returncode: Optional[int]
    stdout: str
    stderr: str
    wall_s: float          # elapsed real time
    cpu_s: float           # user+system CPU time (from the kernel's rusage)
    max_rss_kb: int        # peak resident memory (a cheap "profile")
    signal_name: str = ""  # e.g. SIGSEGV when the program crashed

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def _decode(path: Path) -> str:
    """Read at most MAX_CAPTURE_BYTES of a captured stream as text."""
    with open(path, "rb") as f:
        return f.read(MAX_CAPTURE_BYTES).decode("utf-8", errors="replace")


def run_sandboxed(
    argv: Sequence[str],
    stdin_text: str = "",
    time_limit_s: float = 1.0,
    memory_limit_mb: Optional[int] = 64,
    nproc: Optional[int] = 64,
    env_extra: Optional[dict] = None,
) -> RunResult:
    """
    Execute `argv` with stdin_text under strict limits and return a RunResult.

    memory_limit_mb=None disables the address-space cap.  This is REQUIRED for
    AddressSanitizer builds because ASan reserves terabytes of virtual memory.
    nproc=None disables the process-count cap (LeakSanitizer needs to spawn a
    helper thread).
    """
    cpu_limit = int(time_limit_s) + 1          # RLIMIT_CPU is in whole seconds
    mem_bytes = int(memory_limit_mb) * 1024 * 1024 if memory_limit_mb else None

    with tempfile.TemporaryDirectory(prefix="sbx_") as tmp:
        tmp = Path(tmp)
        in_p, out_p, err_p = tmp / "in.txt", tmp / "out.txt", tmp / "err.txt"
        in_p.write_text(stdin_text, encoding="utf-8", errors="replace")

        # ---- build the command line with limits ---------------------------
        cmd = list(argv)
        preexec = None
        if shutil.which("prlimit"):
            lim = ["prlimit", f"--cpu={cpu_limit}", f"--fsize={OUTPUT_FILE_LIMIT}", "--core=0"]
            if mem_bytes:
                lim.append(f"--as={mem_bytes}")
            if nproc:
                lim.append(f"--nproc={nproc}")
            cmd = lim + ["--"] + cmd
        else:  # fallback path (see module docstring)
            def preexec():  # runs in the child just before exec
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
                resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_LIMIT,) * 2)
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                if mem_bytes:
                    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes,) * 2)
                if nproc:
                    resource.setrlimit(resource.RLIMIT_NPROC, (nproc,) * 2)

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"}
        env.update(env_extra or {})

        start = time.perf_counter()
        with open(in_p, "rb") as fin, open(out_p, "wb") as fout, open(err_p, "wb") as ferr:
            try:
                proc = subprocess.Popen(
                    cmd, stdin=fin, stdout=fout, stderr=ferr, cwd=tmp, env=env,
                    start_new_session=True,      # own process group -> we can kill children too
                    preexec_fn=preexec,
                )
            except OSError as e:
                return RunResult("RUNTIME_ERROR", None, "", f"cannot start program: {e}", 0.0, 0.0, 0)

            # ---- wait with a deadline, using wait4 so we get kernel rusage ----
            deadline = start + time_limit_s
            timed_out = False
            status = rusage = None
            sleep = 0.0005
            while True:
                pid, status, rusage = os.wait4(proc.pid, os.WNOHANG)
                if pid != 0:
                    break
                if time.perf_counter() > deadline:
                    timed_out = True
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)   # kill the whole group
                    except ProcessLookupError:
                        pass
                    _, status, rusage = os.wait4(proc.pid, 0)
                    break
                time.sleep(sleep)
                sleep = min(sleep * 2, 0.01)                  # back off: cheap polling
            wall = time.perf_counter() - start

        # Tell subprocess we already reaped the child (avoids ResourceWarning).
        proc.returncode = os.waitstatus_to_exitcode(status)
        code = proc.returncode
        try:                                   # make sure no grandchild survives
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

        stdout, stderr = _decode(out_p), _decode(err_p)
        cpu = rusage.ru_utime + rusage.ru_stime
        rss = int(rusage.ru_maxrss)            # kilobytes on Linux

        # ---- classify what happened ---------------------------------------
        sig = ""
        if code < 0:
            try:
                sig = signal.Signals(-code).name
            except ValueError:
                sig = f"SIG{-code}"
        if timed_out or sig == "SIGXCPU":
            st = "TIMEOUT"
        elif sig == "SIGXFSZ":
            st = "OUTPUT_LIMIT"
        elif mem_bytes and rss * 1024 >= 0.9 * mem_bytes:
            st = "MEMORY_LIMIT"
        elif code != 0:
            st = "RUNTIME_ERROR"
        else:
            st = "OK"
        return RunResult(st, code, stdout, stderr, wall, cpu, rss, sig)
