"""Agent middleware: dynamic system prompt, human approval, sub-agent guard, limits."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelRequest,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    dynamic_prompt,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ToolMessage

from ..context import AppContext
from .prompts import main_prompt, subagent_prompt
from .safety import GATED_TOOLS, needs_approval


def approval_middleware(ctx: AppContext) -> HumanInTheLoopMiddleware:
    """Interrupt before gated tool calls according to ``settings.approval_mode``.

    ``when`` is a pure function of (mode, tool, args): HumanInTheLoopMiddleware evaluates it
    again on resume, so it must give the same answer for the same call.
    """

    def when_for(name: str) -> Callable[[ToolCallRequest], bool]:
        def when(req: ToolCallRequest) -> bool:
            mode = ctx.settings.approval_mode
            if mode == "never":
                return False
            if mode == "always":
                return True
            return needs_approval(name, req.tool_call["args"], ctx.workspace)

        return when

    config: dict[str, Any] = {
        name: {"allowed_decisions": ["approve", "edit", "reject"], "when": when_for(name)}
        for name in GATED_TOOLS
    }
    return HumanInTheLoopMiddleware(config, description_prefix="Approval required")


class SubagentGuardMiddleware(AgentMiddleware):
    """Helpers can't ask the user, so risky calls are refused (the parent decides what to do)."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        if self.ctx.settings.approval_mode == "never":
            return None
        if call["name"] in GATED_TOOLS and needs_approval(
            call["name"], call["args"], self.ctx.workspace
        ):
            return ToolMessage(
                content=(
                    "REFUSED: this action needs the user's approval and helper agents cannot "
                    "request it. Do not retry; report in your answer what you wanted to run so "
                    "the main agent can do it."
                ),
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> Any:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[..., Awaitable[Any]]
    ) -> Any:
        return self._refusal(request) or await handler(request)


def _main_prompt_middleware(ctx: AppContext) -> AgentMiddleware:
    @dynamic_prompt
    async def system_prompt(request: ModelRequest) -> str:
        return main_prompt(ctx)

    return system_prompt


def _sub_prompt_middleware(ctx: AppContext) -> AgentMiddleware:
    @dynamic_prompt
    async def system_prompt(request: ModelRequest) -> str:
        return subagent_prompt(ctx)

    return system_prompt


def main_middleware(ctx: AppContext, summarizer: BaseChatModel) -> list[AgentMiddleware]:
    return [
        _main_prompt_middleware(ctx),
        TodoListMiddleware(),
        SummarizationMiddleware(
            model=summarizer,
            trigger=("tokens", ctx.settings.summarize_trigger_tokens),
            keep=("messages", 24),
        ),
        ToolCallLimitMiddleware(run_limit=ctx.settings.max_tool_calls_per_run),
        approval_middleware(ctx),
    ]


def sub_middleware(ctx: AppContext) -> list[AgentMiddleware]:
    return [
        _sub_prompt_middleware(ctx),
        ToolCallLimitMiddleware(run_limit=40),
        SubagentGuardMiddleware(ctx),
    ]
