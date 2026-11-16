"""File tools (pure Python: identical behaviour on Windows and Linux)."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from ..agent.safety import is_protected_path, resolve_in_workspace
from ..context import AppContext
from .common import safe

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}
MAX_READ_CHARS = 40_000
MAX_FILE_BYTES = 2_000_000


def _is_binary(path: Path) -> bool:
    with path.open("rb") as f:
        return b"\x00" in f.read(4096)


def make_file_tools(ctx: AppContext) -> list[BaseTool]:
    ws = ctx.workspace

    def resolve(path: str) -> Path:
        p = resolve_in_workspace(path, ws)
        if is_protected_path(p):
            raise PermissionError("this file holds secrets and cannot be accessed by file tools")
        return p

    @tool(parse_docstring=True)
    @safe
    def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
        """Read a text file and return it with line numbers.

        Args:
            path: File path (absolute, or relative to the workspace).
            offset: First line to return, 1-based (default 1).
            limit: Maximum number of lines to return (default 2000).
        """
        p = resolve(path)
        if not p.is_file():
            return f"ERROR: {path!r} is not a file"
        if _is_binary(p):
            return f"ERROR: {path!r} looks like a binary file ({p.stat().st_size} bytes)"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(offset))
        chunk = lines[start - 1 : start - 1 + max(1, int(limit))]
        text = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(chunk))
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n[... output truncated; use offset/limit]"
        end = start + len(chunk) - 1
        return f"{text}\n[lines {start}-{end} of {len(lines)}]" if chunk else "(empty)"

    @tool(parse_docstring=True)
    @safe
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a text file (parent directories are created).

        Args:
            path: File path (absolute, or relative to the workspace).
            content: Full new content of the file.
        """
        p = resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
        return f"Wrote {len(content)} chars to {p}"

    @tool(parse_docstring=True)
    @safe
    def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Replace an exact string in an existing file.

        Fails if old_string is missing, or appears more than once (unless replace_all is true).
        Include enough surrounding context in old_string to make it unique.

        Args:
            path: File path (absolute, or relative to the workspace).
            old_string: Exact text to find (including whitespace and indentation).
            new_string: Replacement text.
            replace_all: Replace every occurrence instead of requiring a unique match.
        """
        p = resolve(path)
        if not p.is_file():
            return f"ERROR: {path!r} is not a file"
        text = p.read_text(encoding="utf-8")
        if not old_string:
            return "ERROR: old_string must not be empty"
        count = text.count(old_string)
        if count == 0:
            return "ERROR: old_string not found"
        if count > 1 and not replace_all:
            return f"ERROR: old_string matches {count} places; add context or set replace_all"
        p.write_text(text.replace(old_string, new_string), encoding="utf-8", newline="")
        return f"Edited {p} ({count} replacement{'s' if count != 1 else ''})"

    @tool(parse_docstring=True)
    @safe
    def list_dir(path: str = ".", recursive: bool = False, max_entries: int = 300) -> str:
        """List the contents of a directory (directories end with '/').

        Args:
            path: Directory (absolute, or relative to the workspace).
            recursive: Also list sub-directories (skips .git, node_modules, .venv ...).
            max_entries: Maximum number of entries to return.
        """
        p = resolve(path)
        if not p.is_dir():
            return f"ERROR: {path!r} is not a directory"
        out: list[str] = []
        if recursive:
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in [*(d + "/" for d in dirs), *sorted(files)]:
                    out.append(str((Path(root) / name).relative_to(p)).replace("\\", "/"))
                    if len(out) >= max_entries:
                        break
                if len(out) >= max_entries:
                    break
        else:
            for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                size = "" if child.is_dir() else f"  ({child.stat().st_size} bytes)"
                out.append(f"{child.name}{'/' if child.is_dir() else ''}{size}")
                if len(out) >= max_entries:
                    break
        suffix = f"\n[stopped at {max_entries} entries]" if len(out) >= max_entries else ""
        return ("\n".join(out) or "(empty directory)") + suffix

    @tool(parse_docstring=True)
    @safe
    def search_files(
        pattern: str, path: str = ".", file_glob: str = "*", max_results: int = 100
    ) -> str:
        """Search file contents with a regular expression (like grep -rn).

        Args:
            pattern: Python regular expression to look for.
            path: Directory (or single file) to search; defaults to the workspace.
            file_glob: Only search files whose name matches this glob, e.g. '*.py'.
            max_results: Maximum number of matching lines to return.
        """
        rx = re.compile(pattern)
        root = resolve(path)
        files: list[Path] = []
        if root.is_file():
            files = [root]
        else:
            for r, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                files += [Path(r) / n for n in names if fnmatch.fnmatch(n, file_glob)]
        hits: list[str] = []
        for f in files:
            try:
                if f.stat().st_size > MAX_FILE_BYTES or is_protected_path(f) or _is_binary(f):
                    continue
                for i, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                ):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return "\n".join(hits) + f"\n[stopped at {max_results} results]"
            except OSError:
                continue
        return "\n".join(hits) or "no matches"

    return [read_file, write_file, edit_file, list_dir, search_files]
