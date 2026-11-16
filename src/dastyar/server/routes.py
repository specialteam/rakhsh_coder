"""REST API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config import fake_model_enabled, save_settings
from ..context import AppContext
from ..models import key_available
from ..store.memory_store import MemoryStoreError
from ..store.skill_store import SkillError, parse_frontmatter
from .auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    cookie_value,
    is_authed,
    token_matches,
)

router = APIRouter(prefix="/api")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


# ------------------------------------------------------------------ auth
class LoginBody(BaseModel):
    token: str


@router.get("/auth/status")
async def auth_status(request: Request) -> dict:
    token = request.app.state.token
    return {"auth_required": bool(token), "authed": is_authed(request, token)}


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    token = request.app.state.token
    if not token:
        return {"ok": True}
    client = request.client.host if request.client else "?"
    throttle = request.app.state.throttle
    if throttle.blocked(client):
        raise HTTPException(429, "too many attempts, wait a minute")
    if not token_matches(body.token, token):
        throttle.fail(client)
        raise HTTPException(401, "wrong token")
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        COOKIE_NAME, cookie_value(token), max_age=COOKIE_MAX_AGE, httponly=True,
        samesite="strict", secure=secure, path="/",
    )  # fmt: skip
    return {"ok": True}


@router.post("/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ------------------------------------------------------------------ models / settings
@router.get("/models")
async def list_models(request: Request) -> dict:
    s = _ctx(request).settings
    return {
        "default": s.default_model,
        "models": [
            {
                "name": p.name, "provider": p.provider, "model": p.model,
                "available": key_available(p) or fake_model_enabled(),
            }  # fmt: skip
            for p in s.models
        ],
    }


class SettingsBody(BaseModel):
    approval_mode: Literal["always", "dangerous", "never"] | None = None
    default_model: str | None = None


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    s = _ctx(request).settings
    return {
        "approval_mode": s.approval_mode,
        "default_model": s.default_model,
        "fake_model": fake_model_enabled(),
        "reviewer_enabled": s.reviewer.enabled,
    }


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request) -> dict:
    ctx = _ctx(request)
    st = request.app.state
    if (st.active_sessions or st.pending_sessions) and body.approval_mode:
        # A pending approval is re-evaluated on resume with the *current* mode; changing it
        # in between could make a rejected call run. So: no changes while anything is in flight.
        raise HTTPException(409, "cannot change the approval mode while a chat is running or waiting")
    if body.default_model is not None:
        try:
            ctx.settings.profile(body.default_model)
        except KeyError as e:
            raise HTTPException(400, f"unknown model {body.default_model!r}") from e
        ctx.settings.default_model = body.default_model
    if body.approval_mode is not None:
        ctx.settings.approval_mode = body.approval_mode
    save_settings(ctx.settings)
    return await get_settings(request)


# ------------------------------------------------------------------ sessions
class NewSession(BaseModel):
    model: str | None = None


class PatchSession(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None


def _valid_model(ctx: AppContext, name: str | None) -> str:
    try:
        return ctx.settings.profile(name).name
    except KeyError as e:
        raise HTTPException(400, f"unknown model {name!r}") from e


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict]:
    return _ctx(request).sessions.list_sessions()


@router.post("/sessions")
async def create_session(body: NewSession, request: Request) -> dict:
    ctx = _ctx(request)
    return ctx.sessions.create_session(model=_valid_model(ctx, body.model))


@router.get("/sessions/{sid}")
async def get_session(sid: str, request: Request) -> dict:
    ctx = _ctx(request)
    session = ctx.sessions.get_session(sid)
    if not session:
        raise HTTPException(404, "no such session")
    return {"session": session, "messages": ctx.sessions.get_messages(sid)}


@router.patch("/sessions/{sid}")
async def patch_session(sid: str, body: PatchSession, request: Request) -> dict:
    ctx = _ctx(request)
    if not ctx.sessions.get_session(sid):
        raise HTTPException(404, "no such session")
    fields = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.model is not None:
        fields["model"] = _valid_model(ctx, body.model)
    ctx.sessions.update_session(sid, **fields)
    return ctx.sessions.get_session(sid)  # type: ignore[return-value]


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request) -> dict:
    ctx = _ctx(request)
    if sid in request.app.state.active_sessions:
        raise HTTPException(409, "session is running; stop it first")
    ctx.sessions.delete_session(sid)
    saver = getattr(request.app.state, "saver", None)
    if saver is not None:
        await saver.adelete_thread(sid)
    return {"ok": True}


@router.get("/search")
async def search(q: str, request: Request) -> list[dict]:
    return _ctx(request).sessions.search(q, limit=20)


# ------------------------------------------------------------------ memory
class TextBody(BaseModel):
    text: str = Field(max_length=20_000)


@router.get("/memory")
async def get_memory(request: Request) -> dict:
    m = _ctx(request).memory
    out = {}
    for target in ("memory", "user"):
        used, limit = m.usage(target)
        out[target] = {"text": m.raw(target), "used": used, "limit": limit}
    return out


@router.put("/memory/{target}")
async def put_memory(target: Literal["memory", "user"], body: TextBody, request: Request) -> dict:
    try:
        _ctx(request).memory.set_raw(target, body.text)
    except MemoryStoreError as e:
        raise HTTPException(400, str(e)) from e
    return await get_memory(request)


# ------------------------------------------------------------------ skills
@router.get("/skills")
async def list_skills(request: Request) -> list[dict]:
    return _ctx(request).skills.list()


@router.get("/skills/{name}")
async def get_skill(name: str, request: Request) -> dict:
    try:
        return {"name": name, "content": _ctx(request).skills.view(name)}
    except SkillError as e:
        raise HTTPException(404, str(e)) from e


class SkillBody(BaseModel):
    content: str = Field(max_length=60_000)


@router.put("/skills/{name}")
async def put_skill(name: str, body: SkillBody, request: Request) -> dict:
    skills = _ctx(request).skills
    try:
        parse_frontmatter(body.content)
        if name in skills.names():
            skills.edit(name, body.content)
        else:
            skills.create(name, body.content)
    except SkillError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.delete("/skills/{name}")
async def delete_skill(name: str, request: Request) -> dict:
    try:
        _ctx(request).skills.delete(name)
    except SkillError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}
