"""沙箱：local（subprocess）与 msb（microsandbox），后者不可用时降级并记账。"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from harness.models import EventType, ExecResult

ENV_ALLOW = (
    "PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "SYSTEMROOT", "WINDIR",
    "PATHEXT", "COMSPEC", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV",
)


def sandbox_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_ALLOW if name in os.environ}
    if extra:
        env.update(extra)
    return env


def _result(rc: int, out: str, started: float, timed_out: bool = False, truncated: bool = False) -> ExecResult:
    return ExecResult(
        rc=rc,
        stdout=out,
        stderr="",  # 约束：stderr 合并进 stdout，调用方只读一个流
        duration_ms=int((time.perf_counter() - started) * 1000),
        timed_out=timed_out,
        truncated=truncated,
    )


def truncate_output(text: str, limit: int) -> tuple[str, bool]:
    """保留头尾，防止一条命令把内存/DB 打爆；完整输出无法恢复，所以要标记。"""
    if limit <= 0 or len(text) <= limit:
        return text, False
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    marker = f"\n...[truncated {len(text) - head - tail} chars at sandbox limit {limit}]...\n"
    return text[:head] + marker + (text[-tail:] if tail else ""), True


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        return
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        try:
            killpg(getpgid(proc.pid), 9)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


class LocalSandbox:
    def __init__(
        self,
        workspace: str | Path,
        env: dict[str, str] | None = None,
        shell: str = "auto",
        max_output: int = 1_000_000,
    ) -> None:
        self.workspace = Path(workspace)
        self.env = sandbox_env(env)
        self.shell = shell
        self.max_output = max_output

    def _argv(self, cmd: str) -> list[str] | str:
        kind = self.shell if self.shell != "auto" else ("cmd" if os.name == "nt" else "sh")
        # cmd/pwsh 用整串命令行：传列表会被 Python 二次加引号，cmd.exe 解析不了内嵌引号
        if kind == "cmd":
            # 外层再包一层引号：cmd /c 会剥掉首尾引号，否则 "prog" "arg" 会被拼坏
            return f'cmd.exe /c "{cmd}"'
        if kind == "pwsh":
            return f"pwsh -NoProfile -Command {cmd}"
        if kind == "bash":
            return ["bash", "-lc", cmd]
        return ["sh", "-lc", cmd]

    def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def exec(self, cmd: str, cwd: str, timeout: int) -> ExecResult:
        started = time.perf_counter()
        kwargs: dict = {
            "cwd": cwd or str(self.workspace),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": self.env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(self._argv(cmd), **kwargs)
        except OSError as exc:
            return _result(-1, f"exec failed: {exc}", started)

        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # 孙进程还占着管道：放弃读输出，保证调用方按时返回
                proc.kill()
                out = ""
            body, truncated = truncate_output(out or "", self.max_output)
            return _result(-1, body, started, timed_out=True, truncated=truncated)
        body, truncated = truncate_output(out or "", self.max_output)
        return _result(proc.returncode, body, started, truncated=truncated)

    def stop(self) -> None:
        return None


class MicrosandboxSandbox:
    """msb CLI 包装；Windows 需要 WHP，没有就由 make_sandbox 降级。"""

    def __init__(self, workspace: str | Path, name: str, timeout: int = 120) -> None:
        self.workspace = Path(workspace).resolve()
        self.name = name
        self.timeout = timeout

    def _msb(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["msb", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"msb {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
        return proc

    def start(self) -> None:
        if shutil.which("msb") is None:
            raise RuntimeError("msb not found on PATH")
        self._msb("create", "--name", self.name, "--mount", f"{self.workspace}:/workspace")

    def exec(self, cmd: str, cwd: str, timeout: int) -> ExecResult:
        started = time.perf_counter()
        try:
            proc = self._msb("exec", self.name, "--workdir", "/workspace", "--", cmd, timeout=timeout + 5)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            return _result(-1, f"msb exec failed: {exc}", started)
        body, truncated = truncate_output(proc.stdout + proc.stderr, 1_000_000)
        return _result(proc.returncode, body, started, truncated=truncated)

    def stop(self) -> None:
        try:
            self._msb("stop", self.name, timeout=30)
        except (RuntimeError, subprocess.TimeoutExpired):
            # 机器已经没了也算停成功；这是清理路径，不能反过来打断主流程
            return None


def make_sandbox(cfg, store=None, session_id: str | None = None):
    if cfg.sandbox.kind == "msb":
        sb = MicrosandboxSandbox(cfg.workspace, f"h-{session_id or 'session'}", cfg.sandbox.timeout)
        try:
            sb.start()
            return sb
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if store is not None and session_id:
                store.append(
                    session_id,
                    EventType.ERROR,
                    {"where": "sandbox", "message": f"msb unavailable, fallback to local: {exc}", "stack_hash": ""},
                )
    return LocalSandbox(
        cfg.workspace,
        shell=getattr(cfg.sandbox, "shell", "auto"),
        max_output=getattr(cfg.sandbox, "max_output", 1_000_000),
    )
