"""Heuristics deciding which tool calls need human approval in ``dangerous`` mode.

These are *not* a security boundary: a determined (or prompt-injected) model can disguise a
command. Use ``approval_mode: always`` when that matters. The predicates are pure functions of
the tool arguments, which HumanInTheLoopMiddleware requires (it re-evaluates them on resume).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import paths

_DANGEROUS_COMMAND = re.compile(
    "|".join(
        [
            r"\brm\s+(-[a-z]*[rf]|--recursive|--force)",
            r"\brmdir\b", r"\brd\s+/s", r"\bdel\b.*(/[sq]\b|-recurse)", r"\berase\b",
            r"remove-item", r"format-volume", r"\bformat\s+[a-z]:", r"\bmkfs", r"\bdd\s+if=",
            r"\bdiskpart\b", r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
            r"restart-computer", r"stop-computer",
            r"git\s+push\b.*(--force|\s-f\b)", r"git\s+reset\s+--hard", r"git\s+clean\b",
            r"git\s+(checkout|restore)\s+(--\s+)?\.", r"git\s+branch\s+-D",
            r"\bchmod\s+-R", r"\bchown\s+-R", r"\bsudo\b",
            r"(curl|wget|iwr|invoke-webrequest)[^|;]*\|\s*(sh|bash|iex|invoke-expression|python)",
            r"invoke-expression", r"\biex\b", r"\bstart-process\b.*-verb\s+runas",
            r"\breg(\.exe)?\s+(add|delete|import)", r"set-itemproperty\s+.*hk",
            r"new-itemproperty", r"remove-itemproperty", r"set-executionpolicy",
            r"\bnet\s+(user|localgroup)", r"\bschtasks\b", r"\bcrontab\b", r"\bsc(\.exe)?\s+(create|delete|stop)",
            r"\btaskkill\b", r"stop-process", r"\bkill(all)?\b", r"\bpkill\b",
            r"\b(pip3?|uv|pipx|npm|pnpm|yarn|cargo|gem|choco|winget|scoop|brew)\s+(install|add|remove|uninstall|-g)",
            r"\bapt(-get)?\s+(install|remove|purge)", r"\byum\s+(install|remove)",
            r"\bdnf\s+(install|remove)", r"\bpacman\s+-S",
            r">\s*/dev/(sd|nvme|disk)", r":\(\)\s*\{", r"\bnetsh\b", r"\bicacls\b", r"\btakeown\b",
            r"\bcipher\s+/w", r"\bbcdedit\b", r"\bvssadmin\b",
        ]
    ),
    re.IGNORECASE,
)

_DANGEROUS_PYTHON = re.compile(
    r"\b(os\.(remove|unlink|rmdir|removedirs|system|popen|kill|chmod|chown)|shutil\.(rmtree|move)"
    r"|subprocess|socket|ctypes|winreg|eval\s*\(|exec\s*\(|__import__|pathlib.*\.unlink"
    r"|\.rmdir\(|requests\.(post|put|delete)|httpx\.(post|put|delete)|smtplib|ftplib|paramiko)",
    re.IGNORECASE,
)


def is_dangerous_command(command: str) -> bool:
    return bool(_DANGEROUS_COMMAND.search(command or ""))


def is_dangerous_python(code: str) -> bool:
    return bool(_DANGEROUS_PYTHON.search(code or ""))


def resolve_in_workspace(path: str, workspace: Path) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path or ".")))
    if not p.is_absolute():
        p = workspace / p
    return p.resolve()


def is_outside_workspace(path: str, workspace: Path) -> bool:
    try:
        resolve_in_workspace(path, workspace).relative_to(workspace.resolve())
        return False
    except ValueError:
        return True


def is_protected_path(path: Path) -> bool:
    """Files the file tools must never read or write (secrets)."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return resolved == paths.env_path().resolve()


def needs_approval(tool: str, args: dict, workspace: Path) -> bool:
    """Decision used in ``dangerous`` mode (``always`` mode ignores this)."""
    if tool == "terminal":
        return is_dangerous_command(str(args.get("command", "")))
    if tool == "run_python":
        return is_dangerous_python(str(args.get("code", "")))
    if tool in ("write_file", "edit_file"):
        return is_outside_workspace(str(args.get("path", "")), workspace)
    return False


# Tools that are gated by approval modes.
GATED_TOOLS = ("terminal", "run_python", "write_file", "edit_file")
