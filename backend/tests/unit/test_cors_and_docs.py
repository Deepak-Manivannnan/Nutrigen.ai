"""CORS allow-list, production docs switch and error-message hygiene."""
import importlib

from fastapi.testclient import TestClient


def _build_app(monkeypatch, env, origins=""):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("FRONTEND_ORIGINS", origins)
    import backend.app.config as config
    importlib.reload(config)
    import backend.app.main as main
    importlib.reload(main)
    return main.app


def _restore():
    import backend.app.config as config
    import backend.app.main as main
    importlib.reload(config)
    importlib.reload(main)


def _preflight(client, origin):
    return client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_production_allows_only_configured_origin_and_hides_docs(monkeypatch):
    try:
        app = _build_app(monkeypatch, "production", "https://front.example.com")
        client = TestClient(app)
        assert _preflight(client, "https://front.example.com").headers.get("access-control-allow-origin") == "https://front.example.com"
        assert "access-control-allow-origin" not in _preflight(client, "https://evil.example.com").headers
        assert "access-control-allow-origin" not in _preflight(client, "http://localhost:5173").headers
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/health").status_code == 200
    finally:
        monkeypatch.undo()
        _restore()


def test_production_without_origins_allows_nobody(monkeypatch):
    try:
        client = TestClient(_build_app(monkeypatch, "production", ""))
        assert "access-control-allow-origin" not in _preflight(client, "https://front.example.com").headers
    finally:
        monkeypatch.undo()
        _restore()


def test_development_allows_localhost_any_port_but_not_strangers(monkeypatch):
    try:
        client = TestClient(_build_app(monkeypatch, "development", ""))
        assert _preflight(client, "http://localhost:5174").headers.get("access-control-allow-origin") == "http://localhost:5174"
        assert "access-control-allow-origin" not in _preflight(client, "https://evil.example.com").headers
        assert client.get("/docs").status_code == 200
    finally:
        monkeypatch.undo()
        _restore()


def test_debug_logging_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    from backend.app.config import Settings
    assert Settings(_env_file=None).debug is False
