"""Agent graphs: main agent, helper (sub) agent and the background reviewer."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel

from ..context import AppContext
from ..models import build_chat_model, build_cheap_model
from ..tools import build_tools
from .middleware import main_middleware, sub_middleware
from .prompts import review_prompt


def build_subagent(ctx: AppContext, model: BaseChatModel) -> Any:
    return create_agent(
        model,
        build_tools(ctx, "sub"),
        middleware=sub_middleware(ctx),
        name="dastyar_helper",
    )


def build_agent(ctx: AppContext, model_name: str | None, checkpointer: Any) -> Any:
    """Compile the main agent for one model profile (all profiles share the checkpointer)."""
    model = build_chat_model(ctx.settings, model_name)
    summarizer = build_cheap_model(ctx.settings, model_name)
    tools = build_tools(ctx, "main", make_subagent=lambda: build_subagent(ctx, model))
    return create_agent(
        model,
        tools,
        middleware=main_middleware(ctx, summarizer),
        checkpointer=checkpointer,
        name="dastyar",
    )


def build_reviewer(ctx: AppContext, model: BaseChatModel) -> Any:
    """Background reflection agent: may only touch memory and skills."""
    return create_agent(
        model,
        build_tools(ctx, "review"),
        system_prompt=review_prompt(ctx),
        name="dastyar_reviewer",
    )
