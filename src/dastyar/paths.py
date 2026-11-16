"""Filesystem layout of the user's data directory (``~/.dastyar`` by default)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("DASTYAR_HOME")
    return Path(env).expanduser() if env else Path.home() / ".dastyar"


def config_path() -> Path:
    return home() / "config.yaml"


def env_path() -> Path:
    return home() / ".env"


def memories_dir() -> Path:
    return home() / "memories"


def skills_dir() -> Path:
    return home() / "skills"


def workspace_dir() -> Path:
    return home() / "workspace"


def state_db() -> Path:
    """LangGraph checkpoint database."""
    return home() / "state.db"


def sessions_db() -> Path:
    """Chat history + FTS5 index."""
    return home() / "sessions.db"


def ensure_dirs() -> None:
    for d in (home(), memories_dir(), skills_dir(), workspace_dir()):
        d.mkdir(parents=True, exist_ok=True)
