"""WebSocket chat: streams agent events to the browser and takes approvals / stop requests.

Client -> server:  {type: user_message, text, model?} | {type: approval, decisions: [...]} |
                   {type: stop} | {type: ping}
Server -> client:  ready, token, model_done, tool_end, approval_request, subagent, session,
                   review_note, error, done, pong
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command

from ..agent.graph import build_agent
from ..agent.review import run_review
from ..config import fake_model_enabled
from ..models import build_cheap_model

router = APIRouter()

STREAM_MODES = ["messages", "updates", "custom"]
MAX_USER_TEXT = 100_000
DECISION_TYPES = {"approve", "reject", "edit"}


class Conn:
    """Serialised, failure-tolerant sender."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.lock = asyncio.Lock()
        self.closed = False

    async def send(self, obj: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.lock:
            try:
                await self.ws.send_text(json.dumps(obj, ensure_ascii=False, default=str))
            except Exception:  # noqa: BLE001 - client went away
                self.closed = True


async def get_agent(app: FastAPI, model_name: str | None) -> Any:
    ctx = app.state.ctx
    name = model_name or ctx.settings.default_model
    agents = app.state.agents
    if name not in agents:
        async with app.state.agent_lock:
            if name not in agents:
                agents[name] = await asyncio.to_thread(build_agent, ctx, name, app.state.saver)
    return agents[name]


def _canon(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)


def _approval_payload(value: dict[str, Any]) -> dict[str, Any]:
    reviews = value.get("review_configs", [])
    return {
        "type": "approval_request",
        "requests": [
            {
                "name": r.get("name"),
                "args": r.get("args", {}),
                "description": r.get("description", ""),
                "allowed": (reviews[i].get("allowed_decisions") if i < len(reviews) else None)
                or ["approve", "reject"],
            }
            for i, r in enumerate(value.get("action_requests", []))
        ],
    }


class SessionRunner:
    def __init__(self, app: FastAPI, sid: str, conn: Conn) -> None:
        self.app, self.sid, self.conn = app, sid, conn
        self.ctx = app.state.ctx
        self.task: asyncio.Task | None = None
        self.pending_names: list[str] = []
        self.pending_args: list[dict] = []
        self.edits: dict[tuple[str, str], dict] = {}  # (tool, original args) -> user-edited args
        self.calls: dict[str, tuple[str, dict]] = {}
        self.seen_tools: set[str] = set()
        self.turn_tool_calls = 0

    # -- small helpers ---------------------------------------------------------------------
    @property
    def cfg(self) -> dict[str, Any]:
        # A tool call normally consumes at least two LangGraph steps (model -> tools),
        # and middleware/sub-agents can add a few more.  The old fixed value of 150
        # was lower than the configured 80-call run limit, so a legitimate long turn
        # could hit GRAPH_RECURSION_LIMIT before ToolCallLimitMiddleware stopped it.
        max_calls = max(1, int(self.ctx.settings.max_tool_calls_per_run))
        return {
            "configurable": {"thread_id": self.sid},
            "recursion_limit": max(1000, max_calls * 8 + 100),
        }

    @property
    def pending(self) -> bool:
        return bool(self.pending_names)

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def model_name(self) -> str:
        session = self.ctx.sessions.get_session(self.sid) or {}
        return session.get("model") or self.ctx.settings.default_model

    async def error(self, message: str) -> None:
        await self.conn.send({"type": "error", "message": message})

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self.app.state.bg_tasks.add(task)
        task.add_done_callback(self.app.state.bg_tasks.discard)

    def _set_pending(self, value: dict[str, Any]) -> dict[str, Any]:
        payload = _approval_payload(value)
        self.pending_names = [r["name"] for r in payload["requests"]]
        self.pending_args = [r["args"] for r in payload["requests"]]
        self.app.state.pending_sessions.add(self.sid)
        return payload

    def _clear_pending(self) -> None:
        self.pending_names = []
        self.pending_args = []
        self.app.state.pending_sessions.discard(self.sid)

    # -- connection lifecycle --------------------------------------------------------------
    async def announce_pending(self) -> None:
        """After a reload / restart: re-offer an approval that is still waiting in the checkpoint."""
        pending = False
        try:
            agent = await get_agent(self.app, self.model_name())
            state = await agent.aget_state(self.cfg)
            if state.interrupts:
                payload = self._set_pending(state.interrupts[0].value)
                await self.conn.send(payload)
                pending = True
        except Exception:  # noqa: BLE001 - e.g. missing API key: the user finds out on first message
            pass
        await self.conn.send({"type": "ready", "pending_approval": pending})

    async def shutdown(self) -> None:
        if self.busy():
            self.ctx.env.kill_all()
            assert self.task is not None
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self._clear_pending()

    # -- incoming messages -----------------------------------------------------------------
    async def handle(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return await self.error("invalid message")
        kind = msg.get("type")
        if kind == "user_message":
            await self.on_user(msg)
        elif kind == "approval":
            await self.on_approval(msg)
        elif kind == "stop":
            await self.on_stop()
        elif kind == "ping":
            await self.conn.send({"type": "pong"})
        else:
            await self.error(f"unknown message type {kind!r}")

    async def on_user(self, msg: dict[str, Any]) -> None:
        text = str(msg.get("text", "")).strip()
        if not text:
            return
        if len(text) > MAX_USER_TEXT:
            return await self.error("message too long")
        if self.busy() or self.pending:
            return await self.error("the agent is busy or waiting for your approval")
        if self.sid in self.app.state.active_sessions:
            return await self.error("this chat is running in another tab")
        model = msg.get("model")
        if model:
            try:
                self.ctx.settings.profile(model)
            except KeyError:
                return await self.error(f"unknown model {model!r}")
            self.ctx.sessions.update_session(self.sid, model=model)
        self.ctx.sessions.add_message(self.sid, "user", text)
        self.turn_tool_calls = 0
        self.calls.clear()
        self.seen_tools.clear()
        self._start({"messages": [HumanMessage(text)]}, resumed=False)

    def _clean_decisions(self, raw: Any) -> list[dict[str, Any]] | str:
        if not isinstance(raw, list) or len(raw) != len(self.pending_names):
            return f"expected {len(self.pending_names)} decision(s)"
        out: list[dict[str, Any]] = []
        for name, d in zip(self.pending_names, raw, strict=True):
            kind = d.get("type") if isinstance(d, dict) else None
            if kind not in DECISION_TYPES:
                return "invalid decision"
            if kind == "approve":
                out.append({"type": "approve"})
            elif kind == "reject":
                note = str(d.get("message") or "The user rejected this action.")[:2000]
                out.append({"type": "reject", "message": note})
            else:
                action = d.get("edited_action")
                if (
                    not isinstance(action, dict)
                    or action.get("name") != name  # an edit may change args, never the tool
                    or not isinstance(action.get("args"), dict)
                ):
                    return "invalid edited action"
                out.append({"type": "edit", "edited_action": {"name": name, "args": action["args"]}})
        return out

    async def _wait_idle(self) -> None:
        """`approval_request` is sent a moment before the run task actually ends; a fast client
        may answer in that gap, so let the run finish first instead of refusing it."""
        if self.busy():
            assert self.task is not None
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self.task), 15)

    async def on_approval(self, msg: dict[str, Any]) -> None:
        if not self.pending:
            return await self.error("nothing is waiting for approval")
        await self._wait_idle()
        if self.busy():
            return await self.error("already running")
        decisions = self._clean_decisions(msg.get("decisions"))
        if isinstance(decisions, str):
            return await self.error(decisions)
        # Remember what the user changed so the tool card / history show what actually ran.
        for name, original, d in zip(self.pending_names, self.pending_args, decisions, strict=True):
            if d["type"] == "edit":
                self.edits[(name, _canon(original))] = d["edited_action"]["args"]
        self._clear_pending()
        self._start(Command(resume={"decisions": decisions}), resumed=True)

    async def on_stop(self) -> None:
        if self.pending:
            await self._wait_idle()  # a run that is just finishing must not be cancelled mid-interrupt
        if self.busy():
            self.ctx.env.kill_all()  # also ends tool worker threads promptly
            assert self.task is not None
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        elif self.pending:
            decisions = [
                {"type": "reject", "message": "The user pressed Stop; do not continue this task."}
                for _ in self.pending_names
            ]
            self._clear_pending()
            self._start(Command(resume={"decisions": decisions}), resumed=True)

    def _start(self, inputs: Any, resumed: bool) -> None:
        self.app.state.active_sessions.add(self.sid)
        self.task = asyncio.create_task(self._run(inputs, resumed))

    # -- the run ---------------------------------------------------------------------------
    async def _run(self, inputs: Any, resumed: bool) -> None:
        agent: Any = None
        interrupted = stopped = failed = False
        observed_event = False
        attempts = 0
        try:
            agent = await get_agent(self.app, self.model_name())
            if resumed and not self.calls:
                await self._load_calls(agent)
            while True:
                try:
                    async for ns, mode, data in agent.astream(
                        inputs, self.cfg, stream_mode=STREAM_MODES, subgraphs=True
                    ):
                        observed_event = True
                        if await self._on_event(ns, mode, data):
                            interrupted = True
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - retry only safe, pre-stream failures
                    if attempts < 2 and not observed_event and self._transient_error(e):
                        attempts += 1
                        await asyncio.sleep((0.35 * (2 ** (attempts - 1))) + random.random() * 0.2)
                        continue
                    raise
        except asyncio.CancelledError:
            stopped = True
        except Exception as e:  # noqa: BLE001 - report every failure to the user
            failed = True
            await self.error(f"{type(e).__name__}: {e}")
        finally:
            self.app.state.active_sessions.discard(self.sid)
        if (stopped or failed) and agent is not None:
            with contextlib.suppress(Exception):
                await self._repair(agent)
        await self.conn.send(
            {"type": "done", "stopped": stopped, "failed": failed, "interrupted": interrupted}
        )
        if not (stopped or failed or interrupted):
            await self._after_turn(agent)

    @staticmethod
    def _transient_error(exc: Exception) -> bool:
        """Model/network failures that are safe to retry before any stream event."""
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(token in text for token in (
            "connecterror", "connection refused", "connection reset", "timed out",
            "timeout", "temporarily unavailable", "all connection attempts failed",
            "502", "503", "504",
        ))

    async def _on_event(self, ns: tuple, mode: str, data: Any) -> bool:
        """Translate one LangGraph stream item. Returns True when an approval was requested."""
        if mode == "custom":
            if isinstance(data, dict) and data.get("type") == "subagent":
                await self.conn.send(data)
            return False
        if ns:  # nested graphs are not shown (helpers report through custom events)
            return False
        if mode == "messages":
            chunk, meta = data
            if (
                isinstance(chunk, AIMessageChunk)
                and meta.get("langgraph_node") == "model"
                and chunk.text
            ):
                await self.conn.send({"type": "token", "text": chunk.text})
            return False
        if mode != "updates":
            return False
        interrupted = False
        for node, payload in data.items():
            if node == "__interrupt__":
                intr = payload[0] if isinstance(payload, (list, tuple)) else payload
                await self.conn.send(self._set_pending(intr.value))
                interrupted = True
                continue
            if not isinstance(payload, dict):
                continue
            for m in payload.get("messages") or []:
                if node == "model" and isinstance(m, AIMessage):
                    await self._on_model_message(m)
                elif isinstance(m, ToolMessage):
                    await self._on_tool_message(m)
        return interrupted

    async def _on_model_message(self, m: AIMessage) -> None:
        text = m.text.strip()
        if text:
            self.ctx.sessions.add_message(self.sid, "assistant", text)
        calls = [{"id": tc["id"], "name": tc["name"], "args": tc["args"]} for tc in m.tool_calls]
        for c in calls:
            self.calls[c["id"]] = (c["name"], c["args"])
        self.turn_tool_calls += len(calls)
        await self.conn.send({"type": "model_done", "text": text, "tool_calls": calls})

    async def _on_tool_message(self, m: ToolMessage) -> None:
        if m.tool_call_id in self.seen_tools:
            return
        self.seen_tools.add(m.tool_call_id)
        name, args = self.calls.get(m.tool_call_id, (m.name or "tool", {}))
        args = self.edits.pop((name, _canon(args)), args)
        record = {
            "id": m.tool_call_id, "name": name, "args": args,
            "output": m.text, "status": getattr(m, "status", "success"),
        }  # fmt: skip
        self.ctx.sessions.add_message(self.sid, "tool", record)
        await self.conn.send({"type": "tool_end", **record})

    async def _load_calls(self, agent: Any) -> None:
        """After a reload, rebuild id -> (name, args) for the calls awaiting results."""
        state = await agent.aget_state(self.cfg)
        for m in reversed(state.values.get("messages", [])):
            if isinstance(m, HumanMessage):
                break
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    self.calls.setdefault(tc["id"], (tc["name"], tc["args"]))

    async def _repair(self, agent: Any) -> None:
        """Give tool calls that never produced a result a synthetic one, so the thread can go on."""
        state = await agent.aget_state(self.cfg)
        messages = state.values.get("messages", [])
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None:
            return
        missing = [tc for tc in last_ai.tool_calls if tc["id"] not in answered]
        if missing:
            fix = [
                ToolMessage(
                    content="Cancelled: this tool call did not finish.",
                    tool_call_id=tc["id"], name=tc["name"], status="error",
                )  # fmt: skip
                for tc in missing
            ]
            await agent.aupdate_state(self.cfg, {"messages": fix}, as_node="tools")

    # -- after a finished turn ---------------------------------------------------------------
    async def _after_turn(self, agent: Any) -> None:
        n = self.ctx.sessions.increment_turns(self.sid)
        session = self.ctx.sessions.get_session(self.sid) or {}
        if not session.get("title"):
            self._spawn(self._title())
        rv = self.ctx.settings.reviewer
        every = rv.every_n_turns
        if rv.enabled and (self.turn_tool_calls >= rv.min_tool_calls or (every and n % every == 0)):
            self._spawn(self._review(agent))

    async def _title(self) -> None:
        first = next(
            (m["content"] for m in self.ctx.sessions.get_messages(self.sid) if m["role"] == "user"),
            "",
        )
        title = (first.strip().splitlines() or [""])[0][:50]
        if first and not fake_model_enabled():
            try:
                model = await asyncio.to_thread(build_cheap_model, self.ctx.settings, self.model_name())
                prompt = (
                    "Write a very short title (max 6 words, same language as the message, no "
                    f"quotes, no punctuation at the end) for a chat that starts with:\n\n{first[:500]}"
                )
                res = await asyncio.wait_for(model.ainvoke([HumanMessage(prompt)]), 30)
                lines = res.text.strip().strip("\"'«»").splitlines()
                if lines and 0 < len(lines[0].strip()) <= 80:
                    title = lines[0].strip()
            except Exception:  # noqa: BLE001 - keep the truncated first line
                pass
        if not title:
            return
        self.ctx.sessions.update_session(self.sid, title=title)
        await self.conn.send({"type": "session", "id": self.sid, "title": title})

    async def _review(self, agent: Any) -> None:
        state = await agent.aget_state(self.cfg)
        model = await asyncio.to_thread(build_cheap_model, self.ctx.settings, self.model_name())
        note = await run_review(self.ctx, model, state.values.get("messages", []))
        if note:
            self.ctx.sessions.add_message(self.sid, "note", note)
            await self.conn.send({"type": "review_note", "text": note})


@router.websocket("/api/chat/{session_id}")
async def chat(ws: WebSocket, session_id: str) -> None:
    app = ws.app
    if not app.state.ctx.sessions.get_session(session_id):
        await ws.close(code=4404)
        return
    await ws.accept()
    runner = SessionRunner(app, session_id, Conn(ws))
    await runner.announce_pending()
    try:
        while True:
            try:
                msg = await ws.receive_json()
            except json.JSONDecodeError:
                await runner.error("invalid JSON")
                continue
            await runner.handle(msg)
    except WebSocketDisconnect:
        pass
    finally:
        runner.conn.closed = True
        await runner.shutdown()
