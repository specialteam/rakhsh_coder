import pytest

from dastyar.context import AppContext


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DASTYAR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DASTYAR_FAKE_MODEL", "1")
    monkeypatch.delenv("DASTYAR_TOKEN", raising=False)
    # Starlette's TestClient always sends "Host: testserver" on WebSocket handshakes.
    monkeypatch.setenv("DASTYAR_ALLOWED_HOSTS", "testserver")
    c = AppContext.build()
    yield c
    c.sessions.close()
