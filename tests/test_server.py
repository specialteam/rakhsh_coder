import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dastyar.server.app import ConfigError, create_app

DANGEROUS_PY = "!py import subprocess; open('ws_touched.txt','w').write('1')"


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx), base_url="http://localhost") as c:
        yield c


def new_session(client):
    return client.post("/api/sessions", json={}).json()


def drain(ws, stop=("done",)):
    """Receive events until one of the stop types; returns all events."""
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in stop:
            return events


def types(events):
    return [e["type"] for e in events]


def open_chat(client, sid):
    ws = client.websocket_connect(f"/api/chat/{sid}")
    ws_ = ws.__enter__()
    ready = ws_.receive_json()
    assert ready["type"] == "ready"
    return ws, ws_, ready


# ---------------------------------------------------------------- REST
def test_sessions_crud_and_models(client):
    s = new_session(client)
    assert s["model"] == "claude-sonnet"
    assert any(x["id"] == s["id"] for x in client.get("/api/sessions").json())
    assert client.patch(f"/api/sessions/{s['id']}", json={"title": "My chat"}).json()["title"] == "My chat"
    assert client.patch(f"/api/sessions/{s['id']}", json={"model": "nope"}).status_code == 400
    assert client.get("/api/sessions/zzz").status_code == 404
    models = client.get("/api/models").json()
    assert models["default"] == "claude-sonnet" and len(models["models"]) >= 5
    assert client.delete(f"/api/sessions/{s['id']}").json() == {"ok": True}
    assert client.get(f"/api/sessions/{s['id']}").status_code == 404


def test_settings_roundtrip_and_validation(client, ctx):
    assert client.put("/api/settings", json={"approval_mode": "always"}).json()["approval_mode"] == "always"
    assert ctx.settings.approval_mode == "always"
    assert client.put("/api/settings", json={"approval_mode": "bogus"}).status_code == 422
    assert client.put("/api/settings", json={"default_model": "nope"}).status_code == 400
    assert client.put("/api/settings", json={"default_model": "claude-opus"}).json()["default_model"] == "claude-opus"
    assert "approval_mode: always" in (ctx.settings and open(__import__("dastyar.paths", fromlist=["x"]).config_path(), encoding="utf-8").read())


def test_memory_and_skills_endpoints(client):
    r = client.put("/api/memory/user", json={"text": "فارسی بنویس\n\nاسم من علی است"})
    assert r.json()["user"]["text"].startswith("فارسی")
    assert client.get("/api/memory").json()["user"]["used"] > 0
    assert client.put("/api/memory/user", json={"text": "x" * 5000}).status_code == 400
    assert client.put("/api/memory/other", json={"text": "x"}).status_code == 422

    body = "---\nname: my-skill\ndescription: Does a thing\n---\n\nStep 1\n"
    assert client.put("/api/skills/my-skill", json={"content": body}).json() == {"ok": True}
    assert client.get("/api/skills").json() == [{"name": "my-skill", "description": "Does a thing"}]
    assert "Step 1" in client.get("/api/skills/my-skill").json()["content"]
    assert client.put("/api/skills/my-skill", json={"content": body.replace("Step 1", "Step 2")}).status_code == 200
    assert client.put("/api/skills/bad", json={"content": "no frontmatter"}).status_code == 400
    for evil in ("..%2Fx", "a%2Fb", "..", "UPPER"):  # traversal / invalid names must never create anything
        assert client.put(f"/api/skills/{evil}", json={"content": body}).status_code != 200
    assert [s["name"] for s in client.get("/api/skills").json()] == ["my-skill"]
    assert client.delete("/api/skills/my-skill").status_code == 200
    assert client.get("/api/skills/my-skill").status_code == 404


# ---------------------------------------------------------------- security
def test_bad_host_header_is_rejected(client):
    assert client.get("/api/models", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/models", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_cross_origin_writes_and_websocket_are_rejected(client):
    r = client.post("/api/sessions", json={}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    same = client.post("/api/sessions", json={}, headers={"origin": "http://localhost"})
    assert same.status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/chat/{same.json()['id']}", headers={"origin": "http://evil.example"}):
            pass


def test_non_loopback_host_requires_token(ctx):
    ctx.settings.host = "0.0.0.0"
    with pytest.raises(ConfigError, match="DASTYAR_TOKEN"):
        create_app(ctx)


def test_token_login_flow(ctx, monkeypatch):
    monkeypatch.setenv("DASTYAR_TOKEN", "s3cret-token")
    ctx.settings.host = "0.0.0.0"
    with TestClient(create_app(ctx), base_url="http://myserver.example") as c:
        assert c.get("/api/auth/status").json() == {"auth_required": True, "authed": False}
        assert c.get("/api/models").status_code == 401
        assert c.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
        sid = None
        r = c.post("/api/auth/login", json={"token": "s3cret-token"})
        assert r.status_code == 200
        set_cookie = r.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "samesite=strict" in set_cookie
        assert c.get("/api/auth/status").json()["authed"] is True
        sid = c.post("/api/sessions", json={}).json()["id"]
        cookie = {"cookie": f"dastyar_auth={c.cookies.get('dastyar_auth')}"}
        with c.websocket_connect(f"/api/chat/{sid}", headers=cookie) as ws:
            assert ws.receive_json()["type"] == "ready"
        c.cookies.clear()
        with pytest.raises(WebSocketDisconnect), c.websocket_connect(f"/api/chat/{sid}"):
            pass
        # brute force throttle
        for _ in range(8):
            c.post("/api/auth/login", json={"token": "nope"})
        assert c.post("/api/auth/login", json={"token": "s3cret-token"}).status_code == 429


def test_static_ui_is_served_without_auth_and_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Content-Security-Policy" in r.text and "script-src 'self'" in r.text
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/app.js").status_code == 200 and client.get("/style.css").status_code == 200
    assert client.get("/api/models").headers["cache-control"] == "no-store"


def test_ui_has_no_inline_handlers_or_external_resources():
    """The CSP forbids them; catching it here is cheaper than debugging a dead button."""
    from pathlib import Path

    import dastyar

    web = Path(dastyar.__file__).parent / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    import re

    assert not re.search(r"\son\w+\s*=", html) and " style=" not in html
    assert "<script>" not in html
    for text in (html, js, (web / "style.css").read_text(encoding="utf-8")):
        assert not re.search(r"https?://(?!www\.w3\.org)", text), "external URL found"
    assert "eval(" not in js and "document.write" not in js


# ---------------------------------------------------------------- websocket chat
def test_ws_unknown_session_closes(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/chat/doesnotexist"):
        pass


def test_ws_plain_chat_streams_persists_and_titles(client):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "سلام دنیا"})
        events = drain(ws)
        streamed = "".join(e["text"] for e in events if e["type"] == "token")
        assert streamed.strip() == "Echo: سلام دنیا"
        assert [e["type"] for e in events if e["type"] in ("model_done", "done")] == ["model_done", "done"]
        assert events[-1] == {"type": "done", "stopped": False, "failed": False, "interrupted": False}
        title_ev = drain(ws, stop=("session",))[-1]
        assert title_ev["title"] == "سلام دنیا"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    data = client.get(f"/api/sessions/{sid}").json()
    assert data["session"]["title"] == "سلام دنیا" and data["session"]["turn_count"] == 1
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    # follow-up in a new connection keeps the thread (checkpoint) alive
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "again"})
        assert drain(ws)[-1]["failed"] is False


def test_ws_tool_call_events(client):
    sid = new_session(client)["id"]
    ws, w, _ = open_chat(client, sid)
    try:
        w.send_json({"type": "user_message", "text": "!sh echo ws-hi"})
        events = drain(w)
        md = next(e for e in events if e["type"] == "model_done")
        assert md["tool_calls"][0]["name"] == "terminal"
        te = next(e for e in events if e["type"] == "tool_end")
        assert te["name"] == "terminal" and "ws-hi" in te["output"] and te["args"] == {"command": "echo ws-hi"}
    finally:
        ws.__exit__(None, None, None)
    tool_rows = [m for m in client.get(f"/api/sessions/{sid}").json()["messages"] if m["role"] == "tool"]
    assert tool_rows[0]["content"]["name"] == "terminal"


def test_ws_empty_and_invalid_input(client):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "   "})  # ignored silently
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "bogus"})
        assert "unknown message type" in ws.receive_json()["message"]
        ws.send_json({"type": "approval", "decisions": []})
        assert "nothing is waiting" in ws.receive_json()["message"]
        ws.send_json({"type": "user_message", "text": "x", "model": "nope"})
        assert "unknown model" in ws.receive_json()["message"]


def test_ws_approval_flow_survives_reload_and_validates_decisions(client, ctx):
    sid = new_session(client)["id"]
    ws, w, _ = open_chat(client, sid)
    w.send_json({"type": "user_message", "text": DANGEROUS_PY})
    events = drain(w)
    req = next(e for e in events if e["type"] == "approval_request")["requests"][0]
    assert req["name"] == "run_python" and req["allowed"] == ["approve", "edit", "reject"]
    assert events[-1]["interrupted"] is True
    assert not (ctx.workspace / "ws_touched.txt").exists()
    # approval mode is frozen while something waits
    assert client.put("/api/settings", json={"approval_mode": "never"}).status_code == 409
    ws.__exit__(None, None, None)  # "reload the page"

    ws, w, ready = open_chat_pending(client, sid)
    try:
        assert ready["pending_approval"] is True
        # malformed / unsafe decisions are refused
        w.send_json({"type": "approval", "decisions": []})
        assert "expected 1" in w.receive_json()["message"]
        w.send_json({"type": "approval", "decisions": [{"type": "nonsense"}]})
        assert "invalid decision" in w.receive_json()["message"]
        swapped = {"type": "edit", "edited_action": {"name": "terminal", "args": {"command": "rm -rf /"}}}
        w.send_json({"type": "approval", "decisions": [swapped]})
        assert "invalid edited action" in w.receive_json()["message"]
        # new message while waiting is refused
        w.send_json({"type": "user_message", "text": "hi"})
        assert "waiting for your approval" in w.receive_json()["message"]
        # finally reject
        w.send_json({"type": "approval", "decisions": [{"type": "reject", "message": "nope"}]})
        events = drain(w)
        assert events[-1]["interrupted"] is False and events[-1]["failed"] is False
        assert "nope" in next(e for e in events if e["type"] == "tool_end")["output"]
        assert not (ctx.workspace / "ws_touched.txt").exists()
    finally:
        ws.__exit__(None, None, None)
    assert client.put("/api/settings", json={"approval_mode": "never"}).status_code == 200  # unfrozen


def open_chat_pending(client, sid):
    """Reconnect: the pending approval is re-sent before 'ready'."""
    ws = client.websocket_connect(f"/api/chat/{sid}")
    w = ws.__enter__()
    first = w.receive_json()
    assert first["type"] == "approval_request"
    ready = w.receive_json()
    assert ready["type"] == "ready"
    return ws, w, ready


def test_ws_fast_client_can_answer_immediately_after_approval_request(client, ctx):
    """Regression: a client replying the instant it sees approval_request (before the run's own
    'done') used to get 'already running'."""
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        for round_ in range(3):
            ws.send_json({"type": "user_message", "text": DANGEROUS_PY})
            drain(ws, stop=("approval_request",))
            ws.send_json({"type": "approval", "decisions": [{"type": "reject", "message": f"r{round_}"}]})
            events = drain(ws)
            if events[-1]["interrupted"]:  # the first run's own 'done' is still queued; read on
                events += drain(ws)
            assert "error" not in types(events), events
            assert events[-1]["failed"] is False and events[-1]["interrupted"] is False
    assert not (ctx.workspace / "ws_touched.txt").exists()


def test_ws_approve_runs_tool(client, ctx):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": DANGEROUS_PY})
        drain(ws)
        ws.send_json({"type": "approval", "decisions": [{"type": "approve"}]})
        events = drain(ws)
        assert events[-1]["failed"] is False
        assert (ctx.workspace / "ws_touched.txt").read_text() == "1"


def test_ws_edited_approval_shows_and_persists_what_actually_ran(client, ctx):
    sid = new_session(client)["id"]
    edited_args = {"code": "open('ws_edited.txt','w').write('e'); print('edited')"}
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": DANGEROUS_PY})
        drain(ws)
        edit = {"type": "edit", "edited_action": {"name": "run_python", "args": edited_args}}
        ws.send_json({"type": "approval", "decisions": [edit]})
        events = drain(ws)
    te = next(e for e in events if e["type"] == "tool_end")
    assert te["args"] == edited_args  # not the model's original call
    assert (ctx.workspace / "ws_edited.txt").exists() and not (ctx.workspace / "ws_touched.txt").exists()
    stored = [m for m in client.get(f"/api/sessions/{sid}").json()["messages"] if m["role"] == "tool"]
    assert stored[0]["content"]["args"] == edited_args


def test_ws_stop_kills_running_tool_and_thread_stays_usable(client):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "!py import time; time.sleep(60)"})
        drain(ws, stop=("model_done",))  # the tool call has been issued and is now executing
        ws.send_json({"type": "stop"})
        end = drain(ws)[-1]
        assert end["stopped"] is True
        ws.send_json({"type": "user_message", "text": "are you still there"})
        events = drain(ws)
        assert events[-1]["failed"] is False
        assert "Echo: are you still there" in "".join(e["text"] for e in events if e["type"] == "token")


def test_ws_stop_while_waiting_for_approval_rejects(client, ctx):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": DANGEROUS_PY})
        drain(ws)
        ws.send_json({"type": "stop"})
        events = drain(ws)
        assert "Stop" in next(e for e in events if e["type"] == "tool_end")["output"]
        assert not (ctx.workspace / "ws_touched.txt").exists()


def test_ws_subagent_progress_events(client):
    sid = new_session(client)["id"]
    with client.websocket_connect(f"/api/chat/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "!delegate !sh echo one | !sh echo two"})
        events = drain(ws, stop=("done",))
        sub = [e for e in events if e["type"] == "subagent"]
        assert {e["index"] for e in sub} == {0, 1} and {"start", "tool", "done"} <= {e["event"] for e in sub}
        # "done" is also emitted when a helper crashes, so check the real results (and that the
        # helpers work under the persistent checkpointer used by the server)
        result = next(e for e in events if e["type"] == "tool_end" and e["name"] == "delegate_task")
        assert "one" in result["output"] and "two" in result["output"] and "ERROR" not in result["output"]
        # a second delegation in the same thread must not leak the first one's tasks into helpers
        ws.send_json({"type": "user_message", "text": "!delegate !sh echo three"})
        second = next(e for e in drain(ws) if e["type"] == "tool_end" and e["name"] == "delegate_task")
        assert "three" in second["output"] and "Helper 2" not in second["output"]


def test_ws_missing_api_key_reports_error_not_crash(ctx, monkeypatch):
    monkeypatch.delenv("DASTYAR_FAKE_MODEL")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(ctx), base_url="http://localhost") as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        with c.websocket_connect(f"/api/chat/{sid}") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user_message", "text": "hi"})
            events = drain(ws)
            assert "ANTHROPIC_API_KEY" in next(e for e in events if e["type"] == "error")["message"]
            assert events[-1]["failed"] is True
            ws.send_json({"type": "ping"})  # connection still alive
            assert ws.receive_json()["type"] == "pong"
