"""Project intelligence tools used by the coding-quality loop."""

from __future__ import annotations

import os
import time
import zipfile
from collections import Counter
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from ..agent.safety import resolve_in_workspace
from ..context import AppContext
from .. import paths
from .common import safe
from .files import SKIP_DIRS

_LANGS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JSX", ".ts": "TypeScript",
    ".tsx": "TSX", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".css": "CSS", ".html": "HTML", ".md": "Markdown", ".sql": "SQL",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".cs": "C#",
}
_MANIFESTS = {"pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml"}
_TEST_MARKERS = ("test", "spec", "__tests__")


def make_quality_tools(ctx: AppContext) -> list[BaseTool]:
    workspace = ctx.workspace

    @tool(parse_docstring=True)
    @safe
    def project_map(path: str = ".", max_files: int = 400) -> str:
        """Build a compact repository map before making a non-trivial code change.

        Args:
            path: Project directory, absolute or relative to the workspace.
            max_files: Maximum files to include in the map.
        """
        root = resolve_in_workspace(path, workspace)
        if not root.is_dir():
            return f"ERROR: {path!r} is not a directory"
        files: list[Path] = []
        for base, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                p = Path(base) / name
                if p.is_symlink():
                    continue
                files.append(p)
                if len(files) >= max(1, min(int(max_files), 1000)):
                    break
            if len(files) >= max(1, min(int(max_files), 1000)):
                break
        counts = Counter(_LANGS.get(p.suffix.lower(), "Other") for p in files)
        manifests = [str(p.relative_to(root)).replace("\\", "/") for p in files if p.name in _MANIFESTS]
        tests = [str(p.relative_to(root)).replace("\\", "/") for p in files
                 if any(marker in p.name.lower() or marker in str(p.parent).lower()
                        for marker in _TEST_MARKERS)]
        entrypoints = [str(p.relative_to(root)).replace("\\", "/") for p in files
                       if p.name.lower() in {"main.py", "app.py", "index.js", "index.ts",
                                             "main.go", "main.rs", "index.html"}]
        lines = [f"ROOT: {root}", f"FILES: {len(files)}", "LANGUAGES: " + ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items()))]
        lines.append("MANIFESTS: " + (", ".join(manifests) or "none"))
        lines.append("TESTS: " + (", ".join(tests[:80]) or "none"))
        lines.append("ENTRYPOINTS: " + (", ".join(entrypoints) or "none"))
        lines.append("FILES:\n" + "\n".join(
            str(p.relative_to(root)).replace("\\", "/") for p in files))
        if len(files) >= max_files:
            lines.append(f"[truncated at {max_files} files]")
        return "\n".join(lines)

    @tool(parse_docstring=True)
    @safe
    def checkpoint_workspace(label: str = "before-change") -> str:
        """Create a recoverable zip checkpoint of the workspace before a broad edit.

        Args:
            label: Short label for the checkpoint filename.
        """
        safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40] or "checkpoint"
        target_dir = paths.home() / "checkpoints"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_label}.zip"
        count = 0
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for base, dirs, names in os.walk(workspace):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
                for name in sorted(names):
                    p = Path(base) / name
                    if p.is_symlink() or p.stat().st_size > 10_000_000:
                        continue
                    rel = p.relative_to(workspace)
                    archive.write(p, str(rel).replace("\\", "/"))
                    count += 1
                    if count >= 2000:
                        break
                if count >= 2000:
                    break
        return f"Checkpoint created: {target} ({count} files)"

    return [project_map, checkpoint_workspace]
