import pytest
import yaml

from dastyar import cli, paths
from dastyar.config import DEFAULT_CONFIG_YAML, ModelProfile, load_settings, save_settings
from dastyar.models import MissingApiKey, build_cheap_model, build_model, key_available


@pytest.fixture
def real_models(monkeypatch):
    monkeypatch.delenv("DASTYAR_FAKE_MODEL", raising=False)


def prof(provider, **kw):
    base = {"name": "p", "provider": provider, "model": "m", "api_key_env": "TEST_KEY"}
    return ModelProfile(**{**base, **kw})


def test_default_config_is_valid_and_complete(ctx):
    s = load_settings()
    assert {p.provider for p in s.models} == {"anthropic", "openrouter", "openai", "ollama"}
    assert s.default_model in [p.name for p in s.models] and s.cheap_model in [p.name for p in s.models]
    assert s.approval_mode == "dangerous" and s.host == "127.0.0.1"
    assert yaml.safe_load(DEFAULT_CONFIG_YAML)["models"]


def test_settings_roundtrip(ctx):
    s = load_settings()
    s.approval_mode = "always"
    save_settings(s)
    assert load_settings().approval_mode == "always"
    with pytest.raises(KeyError):
        s.profile("nope")


def test_all_providers_build_without_network(real_models, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "dummy-not-a-real-key")
    a = build_model(prof("anthropic", model="claude-sonnet-5-5", temperature=0.2))
    assert type(a).__name__ == "ChatAnthropic" and a.model == "claude-sonnet-5-5" and a.max_tokens == 8192

    o = build_model(prof("openai", model="gpt-x", base_url="https://llm.internal/v1"))
    assert type(o).__name__ == "ChatOpenAI" and o.openai_api_base == "https://llm.internal/v1"

    r = build_model(prof("openrouter", model="vendor/model"))
    assert r.openai_api_base == "https://openrouter.ai/api/v1" and r.model_name == "vendor/model"

    ol = build_model(prof("ollama", model="qwen2.5:7b", api_key_env=None, base_url="http://localhost:11434"))
    assert type(ol).__name__ == "ChatOllama" and ol.model == "qwen2.5:7b"


def test_missing_key_gives_actionable_error(real_models, monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    assert not key_available(prof("anthropic"))
    with pytest.raises(MissingApiKey, match="TEST_KEY"):
        build_model(prof("anthropic"))
    assert key_available(prof("ollama", api_key_env=None))


def test_all_default_profiles_bind_tools(ctx, real_models, monkeypatch):
    """Every shipped profile must at least construct and accept our tool schemas.

    (`ctx` must come first: it sets DASTYAR_FAKE_MODEL, which `real_models` then removes.)"""
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "dummy")
    from dastyar.tools import build_tools

    tools = build_tools(ctx, "main", make_subagent=lambda: None)
    assert {"terminal", "run_python", "read_file", "write_file", "edit_file", "list_dir", "search_files",
            "web_search", "web_fetch", "memory", "skills_list", "skill_view", "skill_manage",
            "session_search", "delegate_task", "project_map", "checkpoint_workspace"} == {t.name for t in tools}
    for p in ctx.settings.models:
        model = build_model(p)
        assert type(model).__name__ != "FakeChatModel", "fake model leaked into a real-model test"
        model.bind_tools(tools)  # raises if a schema is unusable for that provider


def test_cheap_model_falls_back_when_no_key(ctx, real_models, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    # cheap model (anthropic) has no key, the chat model (openai) does -> use the chat model
    m = build_cheap_model(ctx.settings, "openai")
    assert type(m).__name__ == "ChatOpenAI"


def test_tool_roles(ctx):
    from dastyar.tools import build_tools

    sub = {t.name for t in build_tools(ctx, "sub")}
    assert "memory" not in sub and "skill_manage" not in sub and "delegate_task" not in sub
    assert {"terminal", "skill_view", "session_search"} <= sub
    assert {t.name for t in build_tools(ctx, "review")} == {
        "project_map", "checkpoint_workspace", "memory", "skills_list", "skill_view", "skill_manage"
    }


# ---------------------------------------------------------------- CLI
def test_cli_init_and_token(ctx, capsys):
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert str(paths.home()) in out and paths.env_path().exists()
    env_text = paths.env_path().read_text()
    assert "ANTHROPIC_API_KEY=" in env_text and "DASTYAR_TOKEN" in env_text
    assert cli.main(["token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 40


def test_cli_init_does_not_overwrite_existing_env(ctx):
    cli.main(["init"])
    paths.env_path().write_text("ANTHROPIC_API_KEY=keep-me\n")
    cli.main(["init"])
    assert paths.env_path().read_text() == "ANTHROPIC_API_KEY=keep-me\n"


def test_cli_serve_refuses_network_bind_without_token(ctx, capsys):
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "8799"]) == 2
    assert "DASTYAR_TOKEN" in capsys.readouterr().err
