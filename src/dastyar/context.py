"""Shared application state handed to tools, agents and the server."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from . import paths
from .config import Settings, load_settings
from .env.local import LocalEnv
from .store.memory_store import MemoryStore
from .store.session_db import SessionDB
from .store.skill_store import SkillStore


@dataclass
class AppContext:
    settings: Settings
    memory: MemoryStore
    skills: SkillStore
    sessions: SessionDB
    env: LocalEnv
    # Serialises background reviews so two of them never edit memory/skills at once.
    review_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def build(cls, settings: Settings | None = None) -> AppContext:
        settings = settings or load_settings()
        paths.ensure_dirs()
        return cls(
            settings=settings,
            memory=MemoryStore(paths.memories_dir()),
            skills=SkillStore(paths.skills_dir()),
            sessions=SessionDB(paths.sessions_db()),
            env=LocalEnv(
                paths.workspace_dir(),
                default_timeout=settings.terminal_timeout,
                output_limit=settings.output_limit,
            ),
        )

    @property
    def workspace(self):
        return self.env.workspace
