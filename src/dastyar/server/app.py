"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .. import paths
from ..context import AppContext
from . import routes, ws
from .auth import LoginThrottle, SecurityMiddleware, configured_token, is_loopback

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ConfigError(RuntimeError):
    pass


def create_app(ctx: AppContext | None = None) -> FastAPI:
    ctx = ctx or AppContext.build()
    token = configured_token()
    if not is_loopback(ctx.settings.host) and not token:
        raise ConfigError(
            f"host is {ctx.settings.host!r} (not loopback) but DASTYAR_TOKEN is not set. "
            "The agent can run shell commands, so a token is mandatory when it is reachable "
            "from the network. Set DASTYAR_TOKEN in ~/.dastyar/.env."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncSqliteSaver.from_conn_string(str(paths.state_db())) as saver:
            await saver.setup()  # tables are otherwise created lazily on first checkpoint
            app.state.saver = saver
            yield
        # shutting down: stop background work and children
        for task in list(app.state.bg_tasks):
            task.cancel()
        ctx.env.kill_all()
        ctx.sessions.close()

    app = FastAPI(title="Dastyar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.state.token = token
    app.state.agents = {}
    app.state.agent_lock = asyncio.Lock()
    app.state.bg_tasks = set()
    app.state.active_sessions = set()  # sessions with a run in flight (one at a time)
    app.state.pending_sessions = set()  # sessions waiting for an approval decision
    app.state.throttle = LoginThrottle()

    extra = {h for h in os.environ.get("DASTYAR_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if not is_loopback(ctx.settings.host):
        extra.add(ctx.settings.host)
    app.add_middleware(SecurityMiddleware, token=token, extra_hosts=extra)

    app.include_router(routes.router)
    app.include_router(ws.router)
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
