"""Procedural memory: skills as ``<skills>/<name>/SKILL.md`` (agentskills.io style).

A skill is a directory with a ``SKILL.md`` whose YAML frontmatter has ``name`` and
``description``; the body holds step-by-step instructions. Extra files may live under the
skill directory (references/, templates/, scripts/ ...).
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_SKILL_CHARS = 40_000
MAX_FILE_CHARS = 100_000
SKILL_FILE = "SKILL.md"


class SkillError(ValueError):
    """Invalid skill operation (message is shown to the model)."""


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split ``---\\nyaml\\n---\\nbody``. Raises SkillError when malformed."""
    text = content.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise SkillError("SKILL.md must start with a YAML frontmatter block ('---').")
    end = text.find("\n---", 4)
    if end == -1:
        raise SkillError("SKILL.md frontmatter is not closed with '---'.")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise SkillError("frontmatter must be a YAML mapping")
    body = text[end + 4 :].lstrip("\n")
    return meta, body


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._lock = threading.RLock()
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------------------------
    def _skill_dir(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SkillError(
                "skill name must be lowercase letters/digits/-/_ (max 64 chars), "
                f"got {name!r}"
            )
        return self.dir / name

    def _validate(self, name: str, content: str) -> None:
        if len(content) > MAX_SKILL_CHARS:
            raise SkillError(f"SKILL.md too large ({len(content)} > {MAX_SKILL_CHARS} chars)")
        meta, body = parse_frontmatter(content)
        if meta.get("name") != name:
            raise SkillError(f"frontmatter 'name' must equal the skill name {name!r}")
        desc = meta.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise SkillError("frontmatter needs a non-empty 'description'")
        if len(desc) > 500:
            raise SkillError("description must be <= 500 chars")
        if not body.strip():
            raise SkillError("SKILL.md body is empty")

    def _safe_file(self, name: str, rel: str) -> Path:
        base = self._skill_dir(name).resolve()
        # Backslashes and drive colons are rejected on every OS so skills stay portable
        # between Windows and Linux (a name like 'C:\x' is an absolute path on one, a file on the other).
        if not rel or "\\" in rel or ":" in rel or Path(rel).is_absolute():
            raise SkillError("file_path must be a relative path with '/' separators inside the skill")
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError as e:
            raise SkillError("file_path escapes the skill directory") from e
        return target

    def _must_exist(self, name: str) -> Path:
        d = self._skill_dir(name)
        if not (d / SKILL_FILE).is_file():
            raise SkillError(f"skill {name!r} does not exist. Existing: {self.names()}")
        return d

    # -- queries ---------------------------------------------------------------------------
    def names(self) -> list[str]:
        with self._lock:
            return sorted(
                p.name for p in self.dir.iterdir() if p.is_dir() and (p / SKILL_FILE).is_file()
            )

    def list(self) -> list[dict[str, str]]:
        out = []
        with self._lock:
            for name in self.names():
                try:
                    meta, _ = parse_frontmatter((self.dir / name / SKILL_FILE).read_text("utf-8"))
                    desc = str(meta.get("description", "")).strip()
                except (SkillError, OSError):
                    desc = "(unreadable SKILL.md)"
                out.append({"name": name, "description": desc})
        return out

    def index_text(self) -> str:
        items = self.list()
        if not items:
            return "(no skills yet)"
        return "\n".join(f"- {s['name']}: {s['description']}" for s in items)

    def view(self, name: str, file_path: str | None = None) -> str:
        with self._lock:
            d = self._must_exist(name)
            if file_path:
                target = self._safe_file(name, file_path)
                if not target.is_file():
                    raise SkillError(f"file {file_path!r} not found in skill {name!r}")
                return target.read_text("utf-8", errors="replace")
            text = (d / SKILL_FILE).read_text("utf-8")
            extra = sorted(
                str(p.relative_to(d)).replace("\\", "/")
                for p in d.rglob("*")
                if p.is_file() and p.name != SKILL_FILE
            )
            if extra:
                text += "\n\n[Supporting files: " + ", ".join(extra) + "]"
            return text

    # -- mutations -------------------------------------------------------------------------
    def create(self, name: str, content: str) -> str:
        with self._lock:
            d = self._skill_dir(name)
            if d.exists():
                raise SkillError(f"skill {name!r} already exists; use action=edit or patch")
            self._validate(name, content)
            d.mkdir(parents=True)
            (d / SKILL_FILE).write_text(content, encoding="utf-8")
            return f"Created skill {name!r}."

    def edit(self, name: str, content: str) -> str:
        with self._lock:
            d = self._must_exist(name)
            self._validate(name, content)
            (d / SKILL_FILE).write_text(content, encoding="utf-8")
            return f"Rewrote skill {name!r}."

    def patch(self, name: str, old: str, new: str, file_path: str | None = None) -> str:
        with self._lock:
            d = self._must_exist(name)
            target = self._safe_file(name, file_path) if file_path else d / SKILL_FILE
            if not target.is_file():
                raise SkillError(f"file {file_path!r} not found in skill {name!r}")
            text = target.read_text("utf-8")
            if not old:
                raise SkillError("old_string must not be empty")
            count = text.count(old)
            if count == 0:
                raise SkillError("old_string not found")
            if count > 1:
                raise SkillError(f"old_string matches {count} places; make it unique")
            updated = text.replace(old, new, 1)
            if target.name == SKILL_FILE and target.parent == d:
                self._validate(name, updated)
            elif len(updated) > MAX_FILE_CHARS:
                raise SkillError("file too large after patch")
            target.write_text(updated, encoding="utf-8")
            return f"Patched {file_path or SKILL_FILE} in {name!r}."

    def write_file(self, name: str, file_path: str, content: str) -> str:
        with self._lock:
            self._must_exist(name)
            if file_path.replace("\\", "/") == SKILL_FILE:
                raise SkillError("use action=edit to change SKILL.md")
            if len(content) > MAX_FILE_CHARS:
                raise SkillError(f"file too large ({len(content)} > {MAX_FILE_CHARS} chars)")
            target = self._safe_file(name, file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {file_path} in skill {name!r}."

    def delete(self, name: str) -> str:
        with self._lock:
            d = self._must_exist(name)
            shutil.rmtree(d)
            return f"Deleted skill {name!r}."
