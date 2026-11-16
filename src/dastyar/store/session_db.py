"""Chat sessions + messages (what the UI shows) with an FTS5 index for cross-session recall.

LangGraph keeps its own checkpoints in ``state.db``; this database is the human-facing log.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    model TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,          -- user | assistant | tool | note
    content TEXT NOT NULL,       -- text, or JSON for role='tool'
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
"""

_INDEXED_ROLES = ("user", "assistant")
MAX_TOOL_OUTPUT_STORED = 4000


def _fts_query(q: str, operator: str) -> str:
    tokens = re.findall(r"\w+", q, flags=re.UNICODE)
    return f" {operator} ".join(f'"{t}"' for t in tokens[:12])


class SessionDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sessions --------------------------------------------------------------------------
    def create_session(self, model: str | None = None, title: str = "") -> dict[str, Any]:
        sid = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id,title,model,created_at,updated_at) VALUES(?,?,?,?,?)",
                (sid, title, model, now, now),
            )
            self._conn.commit()
        return self.get_session(sid)  # type: ignore[return-value]

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_session(self, sid: str, **fields: Any) -> None:
        allowed = {"title", "model", "turn_count"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {cols}, updated_at=? WHERE id=?",
                (*sets.values(), time.time(), sid),
            )
            self._conn.commit()

    def increment_turns(self, sid: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET turn_count=turn_count+1, updated_at=? WHERE id=?",
                (time.time(), sid),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT turn_count FROM sessions WHERE id=?", (sid,)
            ).fetchone()
        return int(row["turn_count"]) if row else 0

    def delete_session(self, sid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self._conn.commit()

    # -- messages --------------------------------------------------------------------------
    def add_message(self, sid: str, role: str, content: str | dict[str, Any]) -> int:
        if role == "tool" and isinstance(content, dict):
            content = {**content}
            out = str(content.get("output", ""))
            if len(out) > MAX_TOOL_OUTPUT_STORED:
                content["output"] = out[:MAX_TOOL_OUTPUT_STORED] + "\n[... truncated]"
            content = json.dumps(content, ensure_ascii=False)
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id,role,content,ts) VALUES(?,?,?,?)",
                (sid, role, str(content), now),
            )
            self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_messages(self, sid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, ts FROM messages WHERE session_id=? ORDER BY id",
                (sid,),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            if item["role"] == "tool":
                try:
                    item["content"] = json.loads(item["content"])
                except json.JSONDecodeError:
                    item["content"] = {"name": "tool", "args": {}, "output": item["content"]}
            out.append(item)
        return out

    # -- search ----------------------------------------------------------------------------
    def search(
        self, query: str, limit: int = 8, exclude_session: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT m.session_id, s.title, m.role, m.ts, "
            "snippet(messages_fts, 0, '[', ']', ' ... ', 24) AS snippet "
            "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            f"WHERE messages_fts MATCH ? AND m.role IN ({','.join('?' * len(_INDEXED_ROLES))}) "
        )
        params: list[Any] = []
        if exclude_session:
            sql += "AND m.session_id != ? "
        sql += "ORDER BY bm25(messages_fts) LIMIT ?"
        for operator in ("AND", "OR"):
            match = _fts_query(query, operator)
            if not match:
                return []
            params = [match, *_INDEXED_ROLES]
            if exclude_session:
                params.append(exclude_session)
            params.append(limit)
            with self._lock:
                try:
                    rows = self._conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if rows:
                return [dict(r) for r in rows]
        return []
