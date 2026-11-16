"""Settings: ``~/.dastyar/config.yaml`` + secrets from ``~/.dastyar/.env``."""

from __future__ import annotations

import os
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from . import paths

Provider = Literal["anthropic", "openai", "openrouter", "ollama"]
ApprovalMode = Literal["always", "dangerous", "never"]


class ModelProfile(BaseModel):
    name: str
    provider: Provider
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ReviewerSettings(BaseModel):
    enabled: bool = True
    min_tool_calls: int = 5  # review a turn if it used at least this many tool calls
    every_n_turns: int = 10  # ...or every N-th turn regardless


class SearchSettings(BaseModel):
    provider: Literal["ddgs", "tavily", "searxng"] = "ddgs"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    searxng_url: str | None = None


class Settings(BaseModel):
    models: list[ModelProfile]
    default_model: str
    cheap_model: str | None = None
    approval_mode: ApprovalMode = "dangerous"
    host: str = "127.0.0.1"
    port: int = 8765
    terminal_timeout: int = 120
    output_limit: int = 20000
    max_tool_calls_per_run: int = 120
    summarize_trigger_tokens: int = 60000
    reviewer: ReviewerSettings = Field(default_factory=ReviewerSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)

    def profile(self, name: str | None) -> ModelProfile:
        wanted = name or self.default_model
        for p in self.models:
            if p.name == wanted:
                return p
        raise KeyError(f"unknown model profile: {wanted!r}")


DEFAULT_CONFIG_YAML = """\
# Dastyar configuration. API keys do NOT go here: put them in ~/.dastyar/.env
# (e.g. ANTHROPIC_API_KEY=..., OPENROUTER_API_KEY=..., OPENAI_API_KEY=...).

default_model: claude-sonnet   # profile used for new chats
cheap_model: claude-haiku      # titles / background review (falls back to the chat model)

# always    -> ask before every terminal/file-write/python call
# dangerous -> ask only for risky-looking calls (heuristic; not a security boundary)
# never     -> never ask
approval_mode: dangerous

host: 127.0.0.1                # non-local host requires DASTYAR_TOKEN
port: 8765

models:
  - name: claude-sonnet
    provider: anthropic
    model: claude-sonnet-5-5
    api_key_env: ANTHROPIC_API_KEY
  - name: claude-opus
    provider: anthropic
    model: claude-opus-5-5
    api_key_env: ANTHROPIC_API_KEY
  - name: claude-haiku
    provider: anthropic
    model: claude-haiku-5-5
    api_key_env: ANTHROPIC_API_KEY
  - name: openrouter-auto
    provider: openrouter          # any OpenRouter slug works in `model`
    model: openrouter/auto
    api_key_env: OPENROUTER_API_KEY
  - name: openai
    provider: openai              # set base_url to use any OpenAI-compatible endpoint
    model: gpt-4o-mini            # change to the model you have access to
    api_key_env: OPENAI_API_KEY
  - name: ollama-local
    provider: ollama
    model: qwen2.5:7b             # change to a model you have pulled
    base_url: http://localhost:11434

reviewer:
  enabled: true
  min_tool_calls: 5
  every_n_turns: 10

search:
  provider: ddgs                  # ddgs (no key) | tavily | searxng
"""


def load_settings() -> Settings:
    """Create the data dir/config on first run, load ``.env`` and parse the config."""
    paths.ensure_dirs()
    cfg = paths.config_path()
    if not cfg.exists():
        cfg.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    load_dotenv(paths.env_path(), override=False)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    return Settings(**data)


def save_settings(settings: Settings) -> None:
    data = settings.model_dump(mode="json", exclude_none=True)
    paths.config_path().write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def fake_model_enabled() -> bool:
    return os.environ.get("DASTYAR_FAKE_MODEL") == "1"
