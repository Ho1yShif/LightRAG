"""Tests for the ``GET /auth-status`` API-key-only-mode signal.

Regression for the WebUI being unusable in API-key-only mode (``DEMO=false``,
``LIGHTRAG_API_KEY`` set, no ``AUTH_ACCOUNTS`` — the render.yaml default). In
that mode the server ignores guest tokens and mandates ``X-API-Key`` on every
protected route, but ``/auth-status`` used to report only ``auth_configured:
false`` / ``auth_mode: "disabled"``. The WebUI therefore auto-entered guest
("Login Free") mode and never prompted for a key, so every data request 403'd.

``/auth-status`` must now expose ``api_key_required`` so the WebUI can tell
"authentication is genuinely off" (fully open) apart from "you must supply an
API key" (API-key-only). These tests pin that flag across the auth modes.
"""

import sys
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient


_ENV_VARS_TO_ISOLATE = (
    "LLM_BINDING",
    "EMBEDDING_BINDING",
    "AUTH_ACCOUNTS",
    "TOKEN_SECRET",
    "LIGHTRAG_API_KEY",
    "WHITELIST_PATHS",
    "LIGHTRAG_API_PREFIX",
    "DEMO",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests hermetic from developer-local .env and global config state."""
    for var in _ENV_VARS_TO_ISOLATE:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUTH_ACCOUNTS", "")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "")
    monkeypatch.setenv("TOKEN_SECRET", "")
    monkeypatch.setenv("WHITELIST_PATHS", "/health,/api/*")
    monkeypatch.setenv("LLM_BINDING", "ollama")
    monkeypatch.setenv("EMBEDDING_BINDING", "ollama")
    monkeypatch.setenv("DEMO", "false")

    import lightrag.api.config as config

    config._global_args = None
    config._initialized = False
    yield
    config._global_args = None
    config._initialized = False


class _FakeLightRAG:
    """Minimal stand-in implementing the async surface create_app touches."""

    def __init__(self, *_args, **_kwargs):
        pass

    def register_role_llm_builder(self, _builder):
        return None

    def set_role_llm_metadata(self, _role, **_metadata):
        return None

    def get_llm_role_config(self):
        return {}

    async def get_llm_queue_status(self, include_base=True):
        return {}

    async def get_embedding_queue_status(self):
        return {}

    async def get_rerank_queue_status(self):
        return {}


class _FakeOllamaAPI:
    def __init__(self, *_args, **_kwargs):
        self.router = APIRouter()


def _build_client(monkeypatch, *, api_key=None):
    """Build an /auth-status-capable TestClient with backend I/O mocked out."""
    from lightrag.api.config import parse_args, initialize_config

    original_argv = sys.argv.copy()
    try:
        sys.argv = ["lightrag-server"]
        args = parse_args()
    finally:
        sys.argv = original_argv
    if api_key is not None:
        args.key = api_key
    initialize_config(args, force=True)

    import lightrag.api.lightrag_server as lightrag_server

    monkeypatch.setattr(lightrag_server, "LightRAG", _FakeLightRAG)
    monkeypatch.setattr(lightrag_server, "check_frontend_build", lambda: (True, False))
    monkeypatch.setattr(
        lightrag_server, "create_document_routes", lambda *_a, **_k: APIRouter()
    )
    monkeypatch.setattr(
        lightrag_server, "create_query_routes", lambda *_a, **_k: APIRouter()
    )
    monkeypatch.setattr(
        lightrag_server, "create_graph_routes", lambda *_a, **_k: APIRouter()
    )
    monkeypatch.setattr(lightrag_server, "OllamaAPI", _FakeOllamaAPI)
    monkeypatch.setattr(
        lightrag_server, "get_namespace_data", AsyncMock(return_value={"busy": False})
    )
    monkeypatch.setattr(lightrag_server, "get_default_workspace", lambda: "default")
    monkeypatch.setattr(
        lightrag_server,
        "cleanup_keyed_lock",
        lambda: {"cleanup_performed": {}, "current_status": {}},
    )

    app = lightrag_server.create_app(args)
    return TestClient(app)


def test_api_key_only_mode_reports_api_key_required(monkeypatch):
    """API key set, no accounts: /auth-status must flag api_key_required."""
    client = _build_client(monkeypatch, api_key="secret-key")

    body = client.get("/auth-status").json()

    # Backward-compatible fields are unchanged for this mode ...
    assert body["auth_configured"] is False
    assert body["auth_mode"] == "disabled"
    # ... but the WebUI now learns a key is mandatory.
    assert body["api_key_required"] is True


def test_fully_open_mode_does_not_require_api_key(monkeypatch):
    """No key, no accounts: genuinely open — api_key_required must be False."""
    client = _build_client(monkeypatch, api_key=None)

    body = client.get("/auth-status").json()

    assert body["auth_configured"] is False
    assert body["auth_mode"] == "disabled"
    assert body["api_key_required"] is False


def test_demo_mode_does_not_require_api_key(monkeypatch):
    """DEMO=true blanks the key: server is public, no key prompt expected."""
    monkeypatch.setenv("DEMO", "true")
    client = _build_client(monkeypatch, api_key="secret-key")

    body = client.get("/auth-status").json()

    assert body["auth_configured"] is False
    assert body["api_key_required"] is False
    assert body["demo_mode"] is True
