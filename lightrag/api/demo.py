"""Read-only public demo mode for the LightRAG API server.

When ``DEMO=true`` the deployment is meant to be a public, no-login demo:
visitors can browse existing knowledge graphs, inspect entities/relations and
run LLM queries, but must not be able to upload, insert, edit, delete or
otherwise mutate server state. Authentication is separately disabled in
``lightrag/api/config.py`` (the key/auth-accounts are blanked when ``DEMO=true``)
so the WebUI loads without prompting for a key; this module layers the
read-only + cost-protection policy on top.

The whole policy lives in one raw-ASGI middleware (``DemoReadOnlyMiddleware``)
rather than a guard dependency scattered across ~15 route declarations. It is
installed by ``create_app`` only when demo mode is enabled. The middleware:

* returns **403** for any request matching the mutating-route deny-list, and
* returns **429** (with ``Retry-After``) when a client IP exceeds the optional
  per-minute rate limit on the LLM-invoking endpoints.

The deny-list is keyed by ``(method, natural_path)`` where *natural path* is the
route path with any configured API prefix / ASGI ``root_path`` stripped, so a
configured ``LIGHTRAG_API_PREFIX`` cannot defeat the block.

The rate limiter is an in-memory fixed-window (60s) counter. This is per
process — it is NOT shared across gunicorn workers or multiple instances. That
is acceptable for a single-instance starter demo; do not rely on it as a hard
global cap.
"""

from __future__ import annotations

import time
from typing import Dict, Tuple

from starlette.responses import JSONResponse

from lightrag.utils import logger


# --- Deny-list of mutating / destructive routes -----------------------------
# Keyed by (HTTP method, natural route path). Values are a short human phrase
# used in the 403 message. Paths are the router-mounted natural paths
# ("/documents" router prefix, "/graph" and "/api" as declared); the middleware
# strips any configured API prefix / root_path before matching.
BLOCKED_ROUTES: Dict[Tuple[str, str], str] = {
    # Document ingestion / management (all mutate or trigger writes)
    ("POST", "/documents/scan"): "scanning for new documents",
    ("POST", "/documents/upload"): "uploading documents",
    ("POST", "/documents/text"): "inserting text",
    ("POST", "/documents/texts"): "inserting text",
    ("POST", "/documents/clear_cache"): "clearing the cache",
    ("POST", "/documents/reprocess_failed"): "reprocessing documents",
    ("POST", "/documents/cancel_pipeline"): "cancelling the pipeline",
    ("DELETE", "/documents"): "clearing all documents",
    ("DELETE", "/documents/delete_document"): "deleting documents",
    # Knowledge-graph mutation
    ("POST", "/graph/entity/edit"): "editing entities",
    ("POST", "/graph/entity/create"): "creating entities",
    ("POST", "/graph/relation/edit"): "editing relations",
    ("POST", "/graph/relation/create"): "creating relations",
    ("POST", "/graph/entities/merge"): "merging entities",
    ("DELETE", "/graph/entity/delete"): "deleting entities",
    ("DELETE", "/graph/relation/delete"): "deleting relations",
}

# LLM-invoking endpoints that are read-only but expensive: rate-limited per IP
# to protect the deployer's provider key on a public demo.
RATE_LIMITED_ROUTES: frozenset[Tuple[str, str]] = frozenset(
    {
        ("POST", "/query"),
        ("POST", "/query/stream"),
        ("POST", "/query/data"),
        ("POST", "/api/chat"),
        ("POST", "/api/generate"),
    }
)


class _FixedWindowRateLimiter:
    """Per-key fixed-window counter (60s windows), in-memory, single-process.

    ``_buckets`` is bounded so a public endpoint cannot be driven to exhaust
    process memory with distinct keys: fully-elapsed windows are purged lazily
    (at most once per window, amortized O(n)/min) and a hard ``MAX_KEYS`` cap is
    enforced as defense-in-depth. Neither affects a key that is actively within
    its window — the ``(allowed, retry_after_seconds)`` contract is unchanged.
    """

    WINDOW_SECONDS = 60.0
    MAX_KEYS = 10_000

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        # key -> (window_start_monotonic, count)
        self._buckets: Dict[str, Tuple[float, int]] = {}
        # Seeded lazily from the first ``now`` seen so __init__ stays free of
        # any wall/monotonic-clock call (keeps the "time passed in" design the
        # tests rely on).
        self._last_purge: float | None = None

    def check(self, key: str, now: float) -> Tuple[bool, int]:
        """Record a hit for ``key`` and report whether it is allowed.

        Returns ``(allowed, retry_after_seconds)``. When over the limit the
        hit is NOT counted and ``retry_after_seconds`` is the whole seconds
        remaining in the current window (>= 1).
        """
        # Purge fully-elapsed windows at most once per window (amortized
        # O(n)/min). Does not touch any window still within WINDOW_SECONDS.
        if self._last_purge is None:
            self._last_purge = now
        if now - self._last_purge >= self.WINDOW_SECONDS:
            self._buckets = {
                k: (start, count)
                for k, (start, count) in self._buckets.items()
                if now - start < self.WINDOW_SECONDS
            }
            self._last_purge = now
        # Hard cap: if still oversized, drop the oldest windows.
        if len(self._buckets) >= self.MAX_KEYS and key not in self._buckets:
            for k in sorted(self._buckets, key=lambda k: self._buckets[k][0])[
                : self.MAX_KEYS // 10
            ]:
                del self._buckets[k]

        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self.WINDOW_SECONDS:
            # Window elapsed — reset.
            start, count = now, 0
        if count >= self.limit:
            retry_after = max(1, int(self.WINDOW_SECONDS - (now - start)) + 1)
            # Keep the existing window so repeated over-limit calls stay blocked.
            self._buckets[key] = (start, count)
            return False, retry_after
        self._buckets[key] = (start, count + 1)
        return True, 0


def _client_ip(scope, trusted_proxy_hops: int = 1) -> str:
    """Best-effort client IP for rate limiting.

    Uses the ``X-Forwarded-For`` entry ``trusted_proxy_hops`` from the RIGHT —
    the value stamped by the nearest trusted proxy (e.g. Render's edge), which a
    client cannot forge. The left-most entries are client-controlled: a visitor
    can send any ``X-Forwarded-For`` they like, so trusting the left-most would
    let them mint a fresh rate-limit bucket per request and bypass the per-IP
    cap entirely. Falls back to the ASGI transport peer when XFF is absent.
    """
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            parts = [
                p.strip() for p in value.decode("latin-1").split(",") if p.strip()
            ]
            if parts:
                idx = max(0, len(parts) - trusted_proxy_hops)
                return parts[idx]
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


class DemoReadOnlyMiddleware:
    """Enforce the read-only + rate-limit policy for public demo deployments.

    Raw-ASGI middleware (mirroring ``_RootPathNormalizationMiddleware`` in
    ``lightrag_server``) so it can short-circuit before any route dependency
    runs and so it composes cleanly with the CORS layer.
    """

    def __init__(
        self,
        app,
        api_prefix: str = "",
        rate_limit_per_minute: int = 0,
        trusted_proxy_hops: int = 1,
    ):
        self.app = app
        self.api_prefix = api_prefix or ""
        self.rate_limit_per_minute = rate_limit_per_minute
        self.trusted_proxy_hops = max(1, trusted_proxy_hops)
        self._limiter = (
            _FixedWindowRateLimiter(rate_limit_per_minute)
            if rate_limit_per_minute and rate_limit_per_minute > 0
            else None
        )
        logger.info(
            "DEMO read-only middleware active (rate limit: %s)",
            f"{rate_limit_per_minute}/min"
            if self._limiter is not None
            else "disabled",
        )

    def _natural_path(self, scope) -> str:
        """Route path with API prefix / root_path stripped, no trailing slash."""
        path = scope.get("path", "") or "/"
        root_path = scope.get("root_path", "") or ""
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :] or "/"
        if self.api_prefix and path.startswith(self.api_prefix):
            path = path[len(self.api_prefix) :] or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        return path

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = self._natural_path(scope)
        key = (method, path)

        operation = BLOCKED_ROUTES.get(key)
        if operation is not None:
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": f"This is a read-only demo; {operation} is disabled."
                },
            )
            await response(scope, receive, send)
            return

        if self._limiter is not None and key in RATE_LIMITED_ROUTES:
            allowed, retry_after = self._limiter.check(
                _client_ip(scope, self.trusted_proxy_hops), time.monotonic()
            )
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            "Demo rate limit exceeded. Please wait a moment and "
                            "try again."
                        )
                    },
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
