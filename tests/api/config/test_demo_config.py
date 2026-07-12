"""Config tests for DEMO mode (lightrag/api/config.py).

DEMO=true must force the server into fully-open auth mode: the effective API
key and auth accounts are blanked so AuthHandler / /auth-status naturally report
"disabled" and issue guest tokens — even when LIGHTRAG_API_KEY / AUTH_ACCOUNTS
are present in the environment. Default (DEMO unset/false) leaves them untouched.
"""

from __future__ import annotations

import sys

import pytest


pytestmark = pytest.mark.offline


_ENV_VARS_TO_ISOLATE = (
    "LLM_BINDING",
    "EMBEDDING_BINDING",
    "LLM_BINDING_HOST",
    "LLM_BINDING_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_BINDING_HOST",
    "EMBEDDING_BINDING_API_KEY",
    "EMBEDDING_MODEL",
    "LIGHTRAG_API_KEY",
    "AUTH_ACCOUNTS",
    "TOKEN_SECRET",
    "DEMO",
    "DEMO_RATE_LIMIT_PER_MINUTE",
    "DEMO_TRUSTED_PROXY_HOPS",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Clear auth/demo env and set minimal valid bindings for parse_args()."""
    for var in _ENV_VARS_TO_ISOLATE:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_BINDING", "openai")
    monkeypatch.setenv("EMBEDDING_BINDING", "openai")


def _parse_args():
    from lightrag.api.config import parse_args

    original_argv = sys.argv.copy()
    try:
        sys.argv = ["lightrag-server"]
        return parse_args()
    finally:
        sys.argv = original_argv


def test_demo_defaults_off():
    """Without DEMO set, the flag is False and auth config is untouched."""
    args = _parse_args()
    assert args.demo is False
    assert args.demo_rate_limit_per_minute == 20


def test_demo_true_forces_open_auth(monkeypatch):
    """DEMO=true blanks the API key even when LIGHTRAG_API_KEY is set."""
    monkeypatch.setenv("DEMO", "true")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "super-secret-key")

    args = _parse_args()

    assert args.demo is True
    assert args.key is None
    assert args.auth_accounts == ""


def test_demo_true_blanks_auth_accounts_and_skips_validation(monkeypatch):
    """DEMO=true blanks AUTH_ACCOUNTS so the TOKEN_SECRET validation (which would
    otherwise raise for accounts without a non-default secret) is bypassed."""
    monkeypatch.setenv("DEMO", "true")
    monkeypatch.setenv("AUTH_ACCOUNTS", "admin:admin123")
    # Deliberately DO NOT set TOKEN_SECRET — non-demo this raises SystemExit.

    args = _parse_args()  # must not raise

    assert args.auth_accounts == ""
    assert args.key is None


def test_demo_rate_limit_parsed(monkeypatch):
    monkeypatch.setenv("DEMO", "true")
    monkeypatch.setenv("DEMO_RATE_LIMIT_PER_MINUTE", "5")

    args = _parse_args()

    assert args.demo_rate_limit_per_minute == 5


def test_demo_trusted_proxy_hops_defaults_to_one():
    args = _parse_args()
    assert args.demo_trusted_proxy_hops == 1


def test_demo_trusted_proxy_hops_override_parses_int(monkeypatch):
    monkeypatch.setenv("DEMO_TRUSTED_PROXY_HOPS", "2")

    args = _parse_args()

    assert args.demo_trusted_proxy_hops == 2


def test_non_demo_keeps_api_key(monkeypatch):
    """With DEMO off, a configured LIGHTRAG_API_KEY survives parsing."""
    monkeypatch.setenv("LIGHTRAG_API_KEY", "keep-me")

    args = _parse_args()

    assert args.demo is False
    assert args.key == "keep-me"
