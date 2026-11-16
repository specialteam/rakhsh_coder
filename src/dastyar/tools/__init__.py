"""Tool registry: which tools each kind of agent gets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import BaseTool

from ..context import AppContext
from .delegate import make_delegate_tool
from .files import make_file_tools
from .knowledge import make_memory_tool, make_session_search_tool, make_skill_tools
from .quality import make_quality_tools
from .terminal import make_terminal_tools
from .web import make_web_tools

Role = Literal["main", "sub", "review"]


def build_tools(
    ctx: AppContext, role: Role = "main", make_subagent: Callable[[], Any] | None = None
) -> list[BaseTool]:
    """main: everything. sub: work tools + read-only knowledge. review: memory + skills only."""
    if role == "review":
        return [*make_quality_tools(ctx), make_memory_tool(ctx), *make_skill_tools(ctx, writable=True)]

    tools: list[BaseTool] = [
        *make_terminal_tools(ctx),
        *make_file_tools(ctx),
        *make_web_tools(ctx),
        *make_skill_tools(ctx, writable=(role == "main")),
        make_session_search_tool(ctx),
        *make_quality_tools(ctx),
    ]
    if role == "main":
        tools.append(make_memory_tool(ctx))
        if make_subagent is not None:
            tools.append(make_delegate_tool(ctx, make_subagent))
    return tools
