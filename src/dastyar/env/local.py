"""Run commands on the local machine: PowerShell on Windows, bash on Linux/macOS.

Uses plain ``subprocess`` (called from worker threads) so it works with any asyncio event loop
policy, kills the whole process tree on timeout and never leaks API keys to child processes.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_SECRET_NAME = re.compile(r"(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)

_PS_PRELUDE = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ProgressPreference='SilentlyContinue';"
)
_PS_EPILOGUE = (
    "\nif ($?) { exit 0 } elseif ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 }"
)


@dataclass
class ExecResult:
    output: str
    exit_code: int | None
    timed_out: bool = False

    def render(self) -> str:
        parts = [self.output.rstrip() or "(no output)"]
        if self.timed_out:
            parts.append("[timed out and was killed]")
        elif self.exit_code not in (0, None):
            parts.append(f"[exit code {self.exit_code}]")
        return "\n".join(parts)


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.4)
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n[... {cut} chars truncated ...]\n{text[-tail:]}"


class LocalEnv:
    def __init__(self, workspace: Path, default_timeout: int = 120, output_limit: int = 20000):
        self.workspace = workspace
        self.default_timeout = default_timeout
        self.output_limit = output_limit
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._active: set[subprocess.Popen] = set()
        self._active_lock = threading.Lock()

    def kill_all(self) -> int:
        """Kill every running child (used by the UI's Stop button). Returns how many."""
        with self._active_lock:
            procs = list(self._active)
        for p in procs:
            self._kill_tree(p)
        return len(procs)

    # -- description for the system prompt --------------------------------------------------
    @staticmethod
    def shell_name() -> str:
        if IS_WINDOWS:
            return "PowerShell (pwsh)" if shutil.which("pwsh") else "Windows PowerShell"
        return "bash" if shutil.which("bash") else "sh"

    @staticmethod
    def os_name() -> str:
        return "Windows" if IS_WINDOWS else sys.platform

    # -- internals -------------------------------------------------------------------------
    @staticmethod
    def _child_env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_COLOR"] = "1"
        return env

    @staticmethod
    def _shell_argv(command: str) -> list[str]:
        if IS_WINDOWS:
            exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
            script = _PS_PRELUDE + command + _PS_EPILOGUE
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            return [
                exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", encoded,
            ]  # fmt: skip
        return [shutil.which("bash") or "/bin/sh", "-c", command]

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=15, check=False,
                )  # fmt: skip
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, subprocess.SubprocessError, OSError):
            pass
        try:
            proc.kill()
        except OSError:
            pass

    def _run_argv(self, argv: list[str], timeout: int | None, cwd: Path | None) -> ExecResult:
        timeout = timeout or self.default_timeout
        popen_kwargs: dict = {}
        if IS_WINDOWS:
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(cwd or self.workspace),
                env=self._child_env(),
                **popen_kwargs,
            )
        except (OSError, ValueError) as e:
            return ExecResult(f"failed to start process: {e}", exit_code=None)
        timed_out = False
        with self._active_lock:
            self._active.add(proc)
        try:
            raw, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_tree(proc)
            try:
                raw, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                raw = b""
        finally:
            with self._active_lock:
                self._active.discard(proc)
        text = (raw or b"").decode("utf-8", errors="replace")
        return ExecResult(
            truncate_middle(text, self.output_limit),
            exit_code=None if timed_out else proc.returncode,
            timed_out=timed_out,
        )

    # -- public API (blocking: call via asyncio.to_thread or from sync tools) ---------------
    def run(self, command: str, timeout: int | None = None, cwd: Path | None = None) -> ExecResult:
        return self._run_argv(self._shell_argv(command), timeout, cwd)

    def run_python(self, code: str, timeout: int | None = None) -> ExecResult:
        tmp_dir = Path(tempfile.gettempdir()) / "dastyar-run"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        script = tmp_dir / f"snippet_{uuid.uuid4().hex[:8]}.py"
        script.write_text(code, encoding="utf-8")
        try:
            return self._run_argv([sys.executable, "-u", str(script)], timeout, None)
        finally:
            script.unlink(missing_ok=True)
