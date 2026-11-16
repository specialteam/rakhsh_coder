"""Build LangChain chat models from config profiles."""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

from .config import ModelProfile, Settings, fake_model_enabled

OPENROUTER_URL = "https://openrouter.ai/api/v1"


class MissingApiKey(RuntimeError):
    pass


def key_available(profile: ModelProfile) -> bool:
    if profile.provider == "ollama" or not profile.api_key_env:
        return True
    return bool(os.environ.get(profile.api_key_env))


def _api_key(profile: ModelProfile) -> str | None:
    if not profile.api_key_env:
        return None
    key = os.environ.get(profile.api_key_env)
    if not key:
        raise MissingApiKey(
            f"Environment variable {profile.api_key_env} is not set "
            f"(needed by model profile '{profile.name}'). Put it in ~/.dastyar/.env"
        )
    return key


def build_model(profile: ModelProfile) -> BaseChatModel:
    if fake_model_enabled():
        from .fake_model import FakeChatModel

        return FakeChatModel()

    extra: dict = {}
    if profile.temperature is not None:
        extra["temperature"] = profile.temperature

    if profile.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {"model": profile.model, "api_key": _api_key(profile), **extra}
        kwargs["max_tokens"] = profile.max_tokens or 8192
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        return ChatAnthropic(**kwargs)

    if profile.provider in ("openai", "openrouter"):
        from langchain_openai import ChatOpenAI

        base_url = profile.base_url or (OPENROUTER_URL if profile.provider == "openrouter" else None)
        kwargs = {"model": profile.model, "api_key": _api_key(profile), **extra}
        if base_url:
            kwargs["base_url"] = base_url
        if profile.max_tokens:
            kwargs["max_tokens"] = profile.max_tokens
        return ChatOpenAI(**kwargs)

    if profile.provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs = {"model": profile.model, **extra}
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        return ChatOllama(**kwargs)

    raise ValueError(f"unsupported provider: {profile.provider}")


def build_chat_model(settings: Settings, name: str | None) -> BaseChatModel:
    return build_model(settings.profile(name))


def build_cheap_model(settings: Settings, fallback: str | None) -> BaseChatModel:
    """Model for side jobs (titles, review). Falls back to the chat model when unset/unusable."""
    for candidate in (settings.cheap_model, fallback, settings.default_model):
        if not candidate:
            continue
        try:
            prof = settings.profile(candidate)
        except KeyError:
            continue
        if key_available(prof) or fake_model_enabled():
            return build_model(prof)
    return build_model(settings.profile(fallback))
