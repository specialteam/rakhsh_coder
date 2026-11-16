"""Memory, skills and cross-session search tools (the "self-improving" part)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from langchain_core.tools import BaseTool, tool

from ..context import AppContext
from ..store.memory_store import MemoryStoreError
from ..store.skill_store import SkillError
from .common import safe


def _current_thread_id() -> str | None:
    try:
        from langgraph.config import get_config

        return get_config().get("configurable", {}).get("thread_id")
    except Exception:  # noqa: BLE001 - outside a graph run
        return None


def make_memory_tool(ctx: AppContext) -> BaseTool:
    @tool(parse_docstring=True)
    @safe
    def memory(
        action: Literal["add", "replace", "remove", "read"],
        target: Literal["memory", "user"] = "memory",
        content: str | None = None,
        match: str | None = None,
    ) -> str:
        """Save or edit durable notes that are shown to you at the start of every future chat.

        Two tiny stores (hard character limits, so keep entries short and merge old ones):
        target='user' = who the user is (name, language, preferences, projects, habits);
        target='memory' = your own notes (environment facts, tool quirks, lessons learned,
        conventions). Only save facts that will still matter in later sessions; never save
        secrets, passwords or one-off task details. Write one fact per entry.

        Args:
            action: add a new entry, replace an existing one, remove one, or read the store.
            target: 'memory' (your notes) or 'user' (profile of the user).
            content: Entry text for add/replace.
            match: For replace/remove: a unique snippet of the existing entry to change.
        """
        try:
            if action == "add":
                return ctx.memory.add(target, content or "")
            if action == "replace":
                return ctx.memory.replace(target, match or "", content or "")
            if action == "remove":
                return ctx.memory.remove(target, match or "")
            return ctx.memory.read(target)
        except MemoryStoreError as e:
            return f"ERROR: {e}"

    return memory


def make_skill_tools(ctx: AppContext, *, writable: bool) -> list[BaseTool]:
    @tool(parse_docstring=True)
    @safe
    def skills_list() -> str:
        """List all saved skills (name and one-line description)."""
        return ctx.skills.index_text()

    @tool(parse_docstring=True)
    @safe
    def skill_view(name: str, file_path: str | None = None) -> str:
        """Read a saved skill's full instructions (or one of its supporting files).

        Always load a skill with this tool before following it.

        Args:
            name: Skill name from the skills list.
            file_path: Optional supporting file inside the skill, e.g. 'references/api.md'.
        """
        try:
            return ctx.skills.view(name, file_path)
        except SkillError as e:
            return f"ERROR: {e}"

    tools = [skills_list, skill_view]
    if not writable:
        return tools

    @tool(parse_docstring=True)
    @safe
    def skill_manage(
        action: Literal["create", "edit", "patch", "delete", "write_file"],
        name: str,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        file_path: str | None = None,
    ) -> str:
        """Create and maintain reusable skills: step-by-step procedures learned from experience.

        Create a skill after finishing a non-trivial task whose method is worth reusing (several
        steps, a workaround you had to discover, a user-specific workflow). A skill is a
        SKILL.md with YAML frontmatter, then concrete instructions:

        ---
        name: <same as the name argument, lowercase-with-dashes>
        description: <one line: when to use this skill>
        ---
        # Title
        1. step ... (exact commands, pitfalls, how to verify)

        Prefer patching an existing skill over creating a near-duplicate. Delete skills that
        turned out to be wrong.

        Args:
            action: create (new skill), edit (rewrite whole SKILL.md), patch (replace old_string
                with new_string), delete, or write_file (add a supporting file).
            name: Skill name (lowercase letters, digits, '-' or '_').
            content: Full SKILL.md text for create/edit, or file text for write_file.
            old_string: For patch: exact text to replace (must be unique).
            new_string: For patch: replacement text.
            file_path: For write_file/patch: relative file inside the skill (e.g. references/x.md).
        """
        try:
            if action == "create":
                return ctx.skills.create(name, content or "")
            if action == "edit":
                return ctx.skills.edit(name, content or "")
            if action == "patch":
                return ctx.skills.patch(name, old_string or "", new_string or "", file_path)
            if action == "delete":
                return ctx.skills.delete(name)
            return ctx.skills.write_file(name, file_path or "", content or "")
        except SkillError as e:
            return f"ERROR: {e}"

    return [*tools, skill_manage]


def make_session_search_tool(ctx: AppContext) -> BaseTool:
    @tool(parse_docstring=True)
    @safe
    def session_search(query: str, limit: int = 6) -> str:
        """Search past conversations (full-text) to recall what was discussed or decided earlier.

        Use it when the user refers to earlier work ("like last time", "that script we wrote")
        or when you suspect you solved a similar problem before.

        Args:
            query: Keywords to look for (words are matched in any order).
            limit: Maximum number of snippets (1-15).
        """
        rows = ctx.sessions.search(
            query, limit=max(1, min(int(limit), 15)), exclude_session=_current_thread_id()
        )
        if not rows:
            return "no matching past conversations"
        lines = []
        for r in rows:
            day = dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
            title = r["title"] or "(untitled)"
            lines.append(f"[{day}] {title} ({r['role']}): {r['snippet']}")
        return "\n".join(lines)

    return session_search
