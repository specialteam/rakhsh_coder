from dastyar.store.session_db import SessionDB


def test_sessions_messages_and_cascade(tmp_path):
    db = SessionDB(tmp_path / "s.db")
    s = db.create_session(model="m1")
    db.add_message(s["id"], "user", "hello")
    db.add_message(s["id"], "tool", {"id": "1", "name": "terminal", "args": {"command": "ls"}, "output": "x" * 9000})
    msgs = db.get_messages(s["id"])
    assert [m["role"] for m in msgs] == ["user", "tool"]
    assert msgs[1]["content"]["name"] == "terminal"
    assert len(msgs[1]["content"]["output"]) < 5000  # stored output is capped
    assert db.increment_turns(s["id"]) == 1
    db.update_session(s["id"], title="T")
    assert db.get_session(s["id"])["title"] == "T"
    db.delete_session(s["id"])
    assert db.get_messages(s["id"]) == [] and db.get_session(s["id"]) is None
    db.close()


def test_fts_search_english_and_persian(tmp_path):
    db = SessionDB(tmp_path / "s.db")
    a = db.create_session(title="Deploy")
    b = db.create_session(title="Chat")
    db.add_message(a["id"], "user", "How do I deploy the website with rsync?")
    db.add_message(a["id"], "assistant", "Use rsync -avz to upload the build folder")
    db.add_message(b["id"], "user", "سلام، میخواهم یک اسکریپت پایتون برای پشتیبان گیری بنویسم")
    r = db.search("rsync deploy")
    assert r and all(x["session_id"] == a["id"] for x in r)
    assert "[" in r[0]["snippet"]  # highlighted match
    fa = db.search("اسکریپت پشتیبان")
    assert fa and fa[0]["session_id"] == b["id"]
    # exclusion of current session + operator fallback (AND finds nothing -> OR)
    assert db.search("rsync", exclude_session=a["id"]) == []
    assert db.search("rsync zzzunknown")  # OR fallback
    assert db.search("   ") == [] and db.search('"""') == []
    db.close()


def test_fts_ignores_tool_rows_and_deleted_messages(tmp_path):
    db = SessionDB(tmp_path / "s.db")
    s = db.create_session()
    db.add_message(s["id"], "tool", {"name": "terminal", "args": {}, "output": "secretword"})
    assert db.search("secretword") == []
    db.add_message(s["id"], "user", "uniqueterm here")
    assert db.search("uniqueterm")
    db.delete_session(s["id"])
    assert db.search("uniqueterm") == []
    db.close()
