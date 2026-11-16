"""Deterministic chat model for tests and key-less UI testing (``DASTYAR_FAKE_MODEL=1``).

Two modes:
* scripted: ``FakeChatModel(script=[AIMessage, ...])`` replays messages in order;
* rule-based (default): reacts to ``!sh``, ``!py``, ``!search``, ``!fetch``, ``!remember``,
  ``!skill`` and ``!delegate`` prefixes in the last user message by emitting a tool call,
  and summarises tool results afterwards.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr

_SKILL_TEMPLATE = (
    "---\nname: {name}\ndescription: Skill created by the fake model\n---\n\n"
    "# {name}\n\nSteps go here.\n"
)


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}"}],
    )


def _rule_based(messages: list[BaseMessage]) -> AIMessage:
    last = messages[-1]
    if isinstance(last, ToolMessage):
        return AIMessage(content=f"Done. Tool result: {str(last.content)[:300]}")
    text = last.text if isinstance(last, HumanMessage) else str(last.content)
    m = re.match(r"!(\w+)\s*(.*)", text, re.DOTALL)
    if not m:
        return AIMessage(content=f"Echo: {text}")
    cmd, rest = m.group(1), m.group(2).strip()
    if cmd == "sh":
        return _call("terminal", {"command": rest})
    if cmd == "py":
        return _call("run_python", {"code": rest})
    if cmd == "search":
        return _call("web_search", {"query": rest})
    if cmd == "fetch":
        return _call("web_fetch", {"url": rest})
    if cmd == "remember":
        return _call("memory", {"action": "add", "target": "memory", "content": rest})
    if cmd == "skill":
        return _call(
            "skill_manage",
            {"action": "create", "name": rest, "content": _SKILL_TEMPLATE.format(name=rest)},
        )
    if cmd == "delegate":
        return _call("delegate_task", {"tasks": [t.strip() for t in rest.split("|") if t.strip()]})
    return AIMessage(content=f"Unknown fake command: {cmd}")


class FakeChatModel(BaseChatModel):
    script: list[AIMessage] | None = None
    seen: list[list[BaseMessage]] = Field(default_factory=list)  # inputs, for tests
    _i: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeChatModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.seen.append(list(messages))
        if self.script is not None:
            msg = self.script[min(self._i, len(self.script) - 1)]
            self._i += 1
            return msg.model_copy(deep=True)
        return _rule_based(messages)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        msg = self._next(messages)
        if msg.tool_calls:
            chunks = [
                {
                    "name": tc["name"],
                    "args": json.dumps(tc["args"]),
                    "id": tc["id"],
                    "index": i,
                    "type": "tool_call_chunk",
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=msg.text, tool_call_chunks=chunks)
            )
            return
        for piece in re.findall(r"\S+\s*|\s+", msg.text) or [""]:
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
