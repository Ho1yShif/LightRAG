"""Tests for read-only demo enforcement (lightrag/api/demo.py + create_app wiring).

Two layers:

* Unit tests drive ``DemoReadOnlyMiddleware`` around a tiny Starlette app so the
  block-list / rate-limit / prefix-stripping logic is exercised without the full
  LightRAG server or any LLM provider.
* One integration test builds the real app via ``create_app`` (with LightRAG
  mocked) to prove the middleware is actually installed and auth is forced open
  when DEMO=true.
"""

from __future__ import annotations

import sys

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from lightrag.api.demo import (
    BLOCKED_ROUTES,
    RATE_LIMITED_ROUTES,
    DemoReadOnlyMiddleware,
    _FixedWindowRateLimiter,
    _client_ip,
)


pytestmark = pytest.mark.offline


# --- Unit: middleware block-list + rate limit over a stub app ---------------


async def _ok(request):  # pragma: no cover - trivial
    return PlainTextResponse("ok")


def _build_client(
    rate_limit_per_minute: int = 0,
    api_prefix: str = "",
    trusted_proxy_hops: int = 1,
) -> TestClient:
    # Mount every route the middleware cares about so allowed requests hit 200
    # (blocked ones short-circuit before routing, so they need no route).
    paths = {
        ("POST", "/query"),
        ("POST", "/query/stream"),
        ("POST", "/query/data"),
        ("POST", "/api/chat"),
        ("POST", "/api/generate"),
        ("GET", "/graphs"),
        ("GET", "/documents"),
        ("POST", "/documents/paginated"),
    } | set(BLOCKED_ROUTES)
    routes = [Route(path, _ok, methods=[method]) for method, path in paths]
    app = Starlette(routes=routes)
    app.add_middleware(
        DemoReadOnlyMiddleware,
        api_prefix=api_prefix,
        rate_limit_per_minute=rate_limit_per_minute,
        trusted_proxy_hops=trusted_proxy_hops,
    )
    return TestClient(app)


@pytest.mark.parametrize("method,path", sorted(BLOCKED_ROUTES))
def test_blocked_routes_return_403(method, path):
    client = _build_client()
    resp = client.request(method, path)
    assert resp.status_code == 403
    assert "read-only demo" in resp.json()["detail"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/query"),
        ("POST", "/query/data"),
        ("GET", "/graphs"),
        ("GET", "/documents"),
        ("POST", "/documents/paginated"),
    ],
)
def test_read_routes_pass_through(method, path):
    client = _build_client()
    resp = client.request(method, path)
    assert resp.status_code == 200


def test_rate_limit_returns_429_after_limit():
    client = _build_client(rate_limit_per_minute=3)
    codes = [client.post("/query", json={}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    # 429 carries a Retry-After header.
    assert client.post("/query", json={}).headers.get("Retry-After")


def test_rate_limit_disabled_when_zero():
    client = _build_client(rate_limit_per_minute=0)
    codes = [client.post("/query", json={}).status_code for _ in range(10)]
    assert all(code == 200 for code in codes)


def test_rate_limit_does_not_apply_to_reads():
    client = _build_client(rate_limit_per_minute=2)
    codes = [client.get("/graphs").status_code for _ in range(10)]
    assert all(code == 200 for code in codes)


def test_blocked_route_short_circuits_before_rate_limit():
    """A mutating route is 403 even under a rate limiter (never reaches it)."""
    client = _build_client(rate_limit_per_minute=1)
    assert client.post("/documents/scan").status_code == 403


# --- Unit: prefix / root_path aware path matching ---------------------------


def _make_scope(method, path, root_path=""):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "headers": [],
        "client": ("1.2.3.4", 5678),
    }


@pytest.mark.parametrize(
    "path,root_path",
    [
        ("/documents/scan", ""),
        ("/site01/documents/scan", "/site01"),  # verbatim-forwarding proxy
        ("/documents/scan", "/site01"),  # proxy stripped the prefix
    ],
)
def test_natural_path_strips_prefix(path, root_path):
    mw = DemoReadOnlyMiddleware(None, api_prefix="/site01")
    scope = _make_scope("POST", path, root_path)
    assert mw._natural_path(scope) == "/documents/scan"


def test_natural_path_strips_trailing_slash():
    mw = DemoReadOnlyMiddleware(None)
    assert mw._natural_path(_make_scope("GET", "/graphs/")) == "/graphs"


def test_configured_prefix_cannot_defeat_block():
    """With a prefix configured, the prefixed mutating path is still blocked."""
    client = _build_client(api_prefix="/site01")
    # Starlette strips root_path; simulate the proxy-forwarded prefixed path by
    # asserting via the middleware's own matching on a prefixed scope.
    mw = DemoReadOnlyMiddleware(None, api_prefix="/site01")
    scope = _make_scope("POST", "/site01/documents/upload", "/site01")
    assert (
        "POST",
        mw._natural_path(scope),
    ) in BLOCKED_ROUTES
    # And the unprefixed client still blocks normally.
    assert client.post("/documents/upload").status_code == 403


# --- Unit: fixed-window rate limiter ----------------------------------------


def test_fixed_window_limiter_resets_after_window():
    limiter = _FixedWindowRateLimiter(limit_per_minute=2)
    t = 1000.0
    assert limiter.check("ip", t) == (True, 0)
    assert limiter.check("ip", t) == (True, 0)
    allowed, retry = limiter.check("ip", t)
    assert allowed is False and retry >= 1
    # Advance past the 60s window → allowed again.
    assert limiter.check("ip", t + 61) == (True, 0)


def test_fixed_window_limiter_is_per_key():
    limiter = _FixedWindowRateLimiter(limit_per_minute=1)
    t = 0.0
    assert limiter.check("a", t)[0] is True
    assert limiter.check("b", t)[0] is True  # different key, own budget
    assert limiter.check("a", t)[0] is False


# --- Unit: client-IP selection from X-Forwarded-For (right-most / hops) ------


def _scope_with_xff(xff: str | None, client=("10.0.0.1", 1234)):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("latin-1")))
    scope = {"type": "http", "headers": headers}
    if client is not None:
        scope["client"] = client
    return scope


def test_client_ip_single_xff_one_hop():
    assert _client_ip(_scope_with_xff("9.9.9.9"), 1) == "9.9.9.9"


def test_client_ip_uses_rightmost_entry_one_hop():
    # Client prepends a spoofed value; edge appends the real IP → rightmost.
    scope = _scope_with_xff("1.1.1.1, 9.9.9.9")
    assert _client_ip(scope, 1) == "9.9.9.9"


def test_client_ip_hop_count_two_picks_second_from_right():
    scope = _scope_with_xff("1.1.1.1, 9.9.9.9, 8.8.8.8")
    assert _client_ip(scope, 2) == "9.9.9.9"


def test_client_ip_hops_exceeding_entries_clamps_to_leftmost():
    scope = _scope_with_xff("7.7.7.7, 9.9.9.9")
    assert _client_ip(scope, 5) == "7.7.7.7"


def test_client_ip_falls_back_to_transport_peer_without_xff():
    assert _client_ip(_scope_with_xff(None, client=("5.6.7.8", 42)), 1) == "5.6.7.8"


def test_client_ip_unknown_without_xff_or_peer():
    assert _client_ip(_scope_with_xff(None, client=None), 1) == "unknown"


def test_spoofed_xff_cannot_bypass_rate_limit():
    """A client sending a fresh left-most XFF per request still shares the
    edge-stamped right-most bucket, so the per-IP limit still bites."""
    client = _build_client(rate_limit_per_minute=3, trusted_proxy_hops=1)
    codes = []
    for i in range(5):
        # Attacker rotates the left-most value; the trusted edge (rightmost)
        # is constant.
        headers = {"X-Forwarded-For": f"9.9.9.{i}, 203.0.113.7"}
        codes.append(client.post("/query", json={}, headers=headers).status_code)
    assert codes == [200, 200, 200, 429, 429]


# --- Unit: rate-limiter memory is bounded (purge + hard cap) ----------------


def test_limiter_purges_expired_windows():
    limiter = _FixedWindowRateLimiter(limit_per_minute=1)
    t = 1000.0
    for i in range(50):
        limiter.check(f"ip-{i}", t)
    assert len(limiter._buckets) == 50
    # Advance a full window past the last purge point → expired windows dropped
    # when the next check runs.
    limiter.check("fresh", t + 61)
    assert len(limiter._buckets) == 1
    assert "fresh" in limiter._buckets


def test_limiter_purge_does_not_evict_active_window():
    limiter = _FixedWindowRateLimiter(limit_per_minute=5)
    t = 0.0
    limiter.check("old", t)
    # "active" starts partway through; still within its window at purge time.
    limiter.check("active", t + 40)
    # Trigger a purge just after "old" expires but while "active" is alive.
    allowed, _ = limiter.check("active", t + 61)
    assert allowed is True
    assert "old" not in limiter._buckets
    assert "active" in limiter._buckets
    # "active" kept its running count (window not reset by the purge).
    assert limiter._buckets["active"][1] == 2


def test_limiter_hard_cap_bounds_bucket_count():
    limiter = _FixedWindowRateLimiter(limit_per_minute=1)
    limiter.MAX_KEYS = 100  # instance-level override keeps the test fast
    t = 1000.0
    # All within one window so the periodic purge never fires — only the hard
    # cap can bound growth here.
    for i in range(500):
        limiter.check(f"ip-{i}", t)
    assert len(limiter._buckets) <= limiter.MAX_KEYS


# --- Integration: create_app installs middleware + forces open auth ----------

_INTEGRATION_ENV = {
    "LLM_BINDING": "openai",
    "EMBEDDING_BINDING": "openai",
    "LLM_BINDING_API_KEY": "sk-test",
    "EMBEDDING_BINDING_API_KEY": "sk-test",
}


@pytest.fixture
def demo_app(monkeypatch):
    from unittest.mock import MagicMock, patch

    for key in (
        "LIGHTRAG_API_PREFIX",
        "AUTH_ACCOUNTS",
        "TOKEN_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in _INTEGRATION_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEMO", "true")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "should-be-ignored")
    monkeypatch.setenv("DEMO_RATE_LIMIT_PER_MINUTE", "3")

    from lightrag.api.config import initialize_config, parse_args

    original_argv = sys.argv.copy()
    try:
        # Keep argv overridden while the config singleton and lightrag_server
        # module are first imported/initialized — importing the server lazily
        # triggers global_args -> parse_args(), which would otherwise choke on
        # pytest's own argv.
        sys.argv = ["lightrag-server"]
        args = parse_args()
        initialize_config(args, force=True)
        with patch("lightrag.api.lightrag_server.LightRAG") as mock_rag:
            mock_rag.return_value = MagicMock()
            from lightrag.api.lightrag_server import create_app

            app = create_app(args)
    finally:
        sys.argv = original_argv
    return TestClient(app)


def test_demo_auth_status_open_with_guest_token(demo_app):
    resp = demo_app.get("/auth-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_configured"] is False
    assert body["demo_mode"] is True
    assert body["access_token"]  # guest token issued, no key prompt


def test_demo_blocks_mutating_route_without_auth(demo_app):
    # No API key header — open mode — but still blocked as read-only.
    resp = demo_app.post("/documents/scan")
    assert resp.status_code == 403
    assert "read-only demo" in resp.json()["detail"]

    resp = demo_app.request("DELETE", "/documents")
    assert resp.status_code == 403


def test_demo_read_route_not_blocked_by_middleware(demo_app):
    # The mocked LightRAG makes the handler error (4xx/5xx), but the read route
    # must not be blocked by the read-only middleware (403).
    resp = demo_app.get("/graphs?label=*")
    assert resp.status_code != 403


def test_demo_query_rate_limited(demo_app):
    codes = [demo_app.post("/query", json={"query": "hi"}).status_code for _ in range(5)]
    # First 3 reach the (mocked) handler; 4th+ hit the limiter.
    assert 429 in codes
    assert codes[-1] == 429


def _build_app(monkeypatch, demo: bool):
    from unittest.mock import MagicMock, patch

    from lightrag.api.config import initialize_config, parse_args

    for key in ("LIGHTRAG_API_PREFIX", "AUTH_ACCOUNTS", "TOKEN_SECRET", "LIGHTRAG_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in _INTEGRATION_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEMO", "true" if demo else "false")

    original_argv = sys.argv.copy()
    try:
        sys.argv = ["lightrag-server"]
        args = parse_args()
        initialize_config(args, force=True)
        with patch("lightrag.api.lightrag_server.LightRAG") as mock_rag:
            mock_rag.return_value = MagicMock()
            from lightrag.api.lightrag_server import create_app

            return create_app(args)
    finally:
        sys.argv = original_argv


def test_non_demo_does_not_install_middleware(monkeypatch):
    app = _build_app(monkeypatch, demo=False)
    installed = {m.cls.__name__ for m in app.user_middleware}
    assert "DemoReadOnlyMiddleware" not in installed


def test_demo_installs_middleware(monkeypatch):
    app = _build_app(monkeypatch, demo=True)
    installed = {m.cls.__name__ for m in app.user_middleware}
    assert "DemoReadOnlyMiddleware" in installed


# --- Deny-list drift guard --------------------------------------------------
#
# BLOCKED_ROUTES is a deny-list: a future mutating endpoint is allowed in demo
# mode by default until someone remembers to classify it, so a "read-only" demo
# could silently regress to writable. This test fails CI the moment a new
# mutating route appears that is neither blocked, rate-limited, nor an
# explicitly-reviewed safe write — making the drift impossible to miss.

# POST-based endpoints that mutate nothing and are safe to leave open in demo
# mode (reviewed against the current route table). Anyone adding a new mutating
# route must classify it into BLOCKED_ROUTES / RATE_LIMITED_ROUTES or, if it is
# a genuine read/auth POST, add it here — with justification.
KNOWN_SAFE_WRITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/login"),  # auth token issue; no server-state mutation
        ("POST", "/documents/paginated"),  # POST body is a query filter (read)
    }
)


def test_no_unclassified_mutating_routes(monkeypatch):
    from starlette.routing import Route

    app = _build_app(monkeypatch, demo=True)
    mw = DemoReadOnlyMiddleware(None)  # api_prefix="" in this test build
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    classified = BLOCKED_ROUTES.keys() | RATE_LIMITED_ROUTES | KNOWN_SAFE_WRITES

    unclassified = []
    for route in app.routes:
        # Skip mounted sub-apps (WebUI / static) and websocket routes — they
        # carry no HTTP method set the deny-list would cover.
        if not isinstance(route, Route):
            continue
        for method in sorted((route.methods or set()) & mutating):
            natural = mw._natural_path({"path": route.path, "root_path": ""})
            if (method, natural) not in classified:
                unclassified.append((method, natural))

    assert not unclassified, (
        "Unclassified mutating route(s) reachable in demo mode: "
        f"{sorted(unclassified)}. Add each to BLOCKED_ROUTES (if it mutates "
        "state), RATE_LIMITED_ROUTES (LLM-invoking read), or KNOWN_SAFE_WRITES "
        "(reviewed safe read/auth POST) in the appropriate place."
    )
