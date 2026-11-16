"""Bounded persistent memory: MEMORY.md (agent notes) and USER.md (facts about the user).

Entries are separated by a ``§`` line. The files have hard character caps (like Hermes) so the
agent has to consolidate instead of growing the system prompt without limit.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

Target = Literal["memory", "user"]

DELIM = "\n§\n"
LIMITS: dict[str, int] = {"memory": 2200, "user": 1375}
FILES: dict[str, str] = {"memory": "MEMORY.md", "user": "USER.md"}
TITLES: dict[str, str] = {"memory": "MEMORY (your notes)", "user": "USER PROFILE"}


class MemoryStoreError(ValueError):
    """Raised for invalid or over-limit memory operations (message is shown to the model)."""


class MemoryStore:
    def __init__(self, directory: Path, limits: dict[str, int] | None = None) -> None:
        self.dir = directory
        self.limits = {**LIMITS, **(limits or {})}
        self._lock = threading.RLock()
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------------------------
    def _check_target(self, target: str) -> str:
        if target not in FILES:
            raise MemoryStoreError(f"target must be 'memory' or 'user', got {target!r}")
        return target

    def _path(self, target: str) -> Path:
        return self.dir / FILES[self._check_target(target)]

    @staticmethod
    def _clean(content: str | None) -> str:
        text = (content or "").strip()
        if not text:
            raise MemoryStoreError("content must not be empty")
        if "§" in text:
            raise MemoryStoreError("content must not contain the '§' character")
        return text

    def _format_entries(self, target: str, entries: list[str]) -> str:
        if not entries:
            return "(no entries)"
        return "\n".join(f"{i + 1}. {e}" for i, e in enumerate(entries))

    def _commit(self, target: str, entries: list[str]) -> None:
        joined = DELIM.join(entries)
        limit = self.limits[target]
        if len(joined) > limit:
            raise MemoryStoreError(
                f"{FILES[target]} would be {len(joined)} chars, over the {limit} char limit. "
                f"Consolidate: replace/remove old entries first.\nCurrent entries:\n"
                f"{self._format_entries(target, self.entries(target))}"
            )
        self._path(target).write_text(joined, encoding="utf-8")

    def _find(self, target: str, match: str) -> int:
        match = (match or "").strip()
        if not match:
            raise MemoryStoreError("'match' must not be empty")
        entries = self.entries(target)
        hits = [i for i, e in enumerate(entries) if match in e]
        if not hits:
            raise MemoryStoreError(
                f"no entry contains {match!r}. Entries:\n{self._format_entries(target, entries)}"
            )
        if len(hits) > 1:
            raise MemoryStoreError(
                f"{match!r} matches {len(hits)} entries; use a longer, unique snippet. Entries:\n"
                f"{self._format_entries(target, entries)}"
            )
        return hits[0]

    # -- public API ------------------------------------------------------------------------
    def entries(self, target: str) -> list[str]:
        with self._lock:
            p = self._path(target)
            if not p.exists():
                return []
            raw = p.read_text(encoding="utf-8").strip()
            return [e.strip() for e in raw.split("§") if e.strip()] if raw else []

    def usage(self, target: str) -> tuple[int, int]:
        with self._lock:
            return len(DELIM.join(self.entries(target))), self.limits[self._check_target(target)]

    def add(self, target: str, content: str) -> str:
        with self._lock:
            text = self._clean(content)
            entries = self.entries(target)
            if text in entries:
                return "Already present."
            self._commit(target, [*entries, text])
            used, limit = self.usage(target)
            return f"Added. {FILES[target]}: {used}/{limit} chars."

    def replace(self, target: str, match: str, content: str) -> str:
        with self._lock:
            idx = self._find(target, match)
            entries = self.entries(target)
            entries[idx] = self._clean(content)
            self._commit(target, entries)
            used, limit = self.usage(target)
            return f"Replaced entry {idx + 1}. {FILES[target]}: {used}/{limit} chars."

    def remove(self, target: str, match: str) -> str:
        with self._lock:
            idx = self._find(target, match)
            entries = self.entries(target)
            removed = entries.pop(idx)
            self._commit(target, entries)
            return f"Removed: {removed[:80]}"

    def read(self, target: str) -> str:
        with self._lock:
            used, limit = self.usage(target)
            body = self._format_entries(target, self.entries(target))
            return f"{FILES[target]} ({used}/{limit} chars)\n{body}"

    def raw(self, target: str) -> str:
        """Entries as editable text (one blank-line-separated paragraph per entry)."""
        return "\n\n".join(self.entries(target))

    def set_raw(self, target: str, text: str) -> None:
        """Replace everything (used by the UI editor). Paragraphs separated by blank lines."""
        with self._lock:
            parts = [p.strip() for p in (text or "").replace("§", "").split("\n\n")]
            self._commit(self._check_target(target), [p for p in parts if p])

    def snapshot(self) -> str:
        """Text injected into the system prompt."""
        blocks = []
        for target in ("memory", "user"):
            entries = self.entries(target)
            used, limit = self.usage(target)
            body = "\n".join(f"- {e}" for e in entries) if entries else "(empty)"
            blocks.append(f"## {TITLES[target]} [{used}/{limit} chars]\n{body}")
        return "\n\n".join(blocks)
