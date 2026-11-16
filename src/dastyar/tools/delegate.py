"""delegate_task: run focused sub-agents with their own context and return only their results."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool

from ..context import AppContext
from .common import safe

MAX_PARALLEL = 3
SUBAGENT_TIMEOUT = 900
MAX_RESULT_CHARS = 8000


def _noop_writer(_: Any) -> None:
    return None


def _writer() -> Callable[[Any], None]:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # noqa: BLE001 - not inside a graph run (tests)
        return _noop_writer


def make_delegate_tool(ctx: AppContext, make_subagent: Callable[[], Any]) -> BaseTool:
    @tool(parse_docstring=True)
    @safe
    async def delegate_task(tasks: list[str], context: str = "") -> str:
        """Hand independent sub-tasks to helper agents that work in parallel and report back.

        Each helper gets a fresh context, the terminal/file/web tools (no memory editing, no
        asking the user) and returns only its final answer, which keeps your own context small.
        Good for: researching several topics at once, scanning many files, long investigations.
        Bad for: tiny tasks, or tasks that depend on each other's results. Helpers cannot see
        this conversation, so make every task self-contained.

        Args:
            tasks: Up to 3 self-contained task descriptions, one per helper.
            context: Optional shared background text given to every helper.
        """
        tasks = [t.strip() for t in tasks if t and t.strip()][:MAX_PARALLEL]
        if not tasks:
            return "ERROR: no tasks given"
        write = _writer()

        async def run_one(i: int, task: str) -> str:
            write({"type": "subagent", "index": i, "event": "start", "task": task[:300]})
            prompt = task if not context.strip() else f"Background:\n{context}\n\nTask:\n{task}"
            final = ""
            try:
                sub = make_subagent()
                async with asyncio.timeout(SUBAGENT_TIMEOUT):
                    async for update in sub.astream(
                        {"messages": [HumanMessage(prompt)]},
                        {"recursion_limit": 80},
                        stream_mode="updates",
                    ):
                        for node, payload in update.items():
                            if node != "model" or not payload:
                                continue
                            for msg in payload.get("messages", []):
                                if not isinstance(msg, AIMessage):
                                    continue
                                for tc in msg.tool_calls:
                                    write(
                                        {
                                            "type": "subagent", "index": i, "event": "tool",
                                            "name": tc["name"], "args": tc["args"],
                                        }  # fmt: skip
                                    )
                                if not msg.tool_calls:
                                    final = msg.text
            except TimeoutError:
                final = f"ERROR: helper timed out after {SUBAGENT_TIMEOUT}s. Partial: {final}"
            except Exception as e:  # noqa: BLE001
                final = f"ERROR: helper failed: {type(e).__name__}: {e}"
            write({"type": "subagent", "index": i, "event": "done"})
            return final.strip() or "(helper returned no answer)"

        results = await asyncio.gather(*(run_one(i, t) for i, t in enumerate(tasks)))
        parts = []
        for i, (task, res) in enumerate(zip(tasks, results, strict=True), 1):
            if len(res) > MAX_RESULT_CHARS:
                res = res[:MAX_RESULT_CHARS] + "\n[... truncated]"
            parts.append(f"### Helper {i}: {task[:120]}\n{res}")
        return "\n\n".join(parts)

    return delegate_task
