"""Background self-improvement: after a turn, a reviewer agent may save memory / skills."""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from ..context import AppContext
from .graph import build_reviewer

MAX_TRANSCRIPT_CHARS = 14_000
TOOL_OUTPUT_PREVIEW = 700


def last_turn(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages from the latest human message to the end."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return list(messages[i:])
    return list(messages)


def format_transcript(messages: Sequence[BaseMessage], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lines.append(f"USER: {m.text}")
        elif isinstance(m, AIMessage):
            if m.text.strip():
                lines.append(f"ASSISTANT: {m.text}")
            for tc in m.tool_calls:
                args = json.dumps(tc["args"], ensure_ascii=False)
                lines.append(f"ASSISTANT CALLS {tc['name']}({args[:600]})")
        elif isinstance(m, ToolMessage):
            out = str(m.content)
            if len(out) > TOOL_OUTPUT_PREVIEW:
                out = out[:TOOL_OUTPUT_PREVIEW] + " [...]"
            lines.append(f"TOOL RESULT ({m.name}): {out}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit // 3] + "\n[... middle omitted ...]\n" + text[-(limit * 2 // 3) :]
    return text


def _actions(messages: Sequence[BaseMessage]) -> list[str]:
    """Human-readable list of successful memory/skill writes made by the reviewer."""
    calls: dict[str, tuple[str, dict]] = {}
    done: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                calls[tc["id"]] = (tc["name"], tc["args"])
        elif isinstance(m, ToolMessage):
            name, args = calls.get(m.tool_call_id, (m.name or "", {}))
            result = str(m.content)
            if result.startswith("ERROR") or args.get("action") == "read":
                continue
            if name == "memory":
                done.append(f"memory/{args.get('target', 'memory')}: {args.get('action')}")
            elif name == "skill_manage":
                done.append(f"skill {args.get('name')}: {args.get('action')}")
    return done


async def run_review(ctx: AppContext, model: BaseChatModel, messages: Sequence[BaseMessage]) -> str:
    """Run one review. Returns a short note for the UI ('' when nothing was saved).

    Never raises; skipped silently if another review is already running.
    """
    if not ctx.review_lock.acquire(blocking=False):
        return ""
    try:
        transcript = format_transcript(last_turn(messages))
        if not transcript.strip():
            return ""
        agent = build_reviewer(ctx, model)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(f"Transcript of the latest turn:\n\n{transcript}")]},
            {"recursion_limit": 30},
        )
        actions = _actions(result["messages"])
        return "Saved: " + "; ".join(actions) if actions else ""
    except Exception as e:  # noqa: BLE001 - background job must never crash the server
        return f"(review failed: {type(e).__name__}: {e})"
    finally:
        ctx.review_lock.release()
