import io
import json
from email.message import Message

import pytest

from trustforge import web


def _request_headers(cookie=None):
    body = json.dumps(
        {
            "coin": "BTC",
            "mode": "risk",
            "question": "Assess risk",
            "locale": "zh-Hant",
            "fresh": False,
        }
    ).encode()
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Content-Length"] = str(len(body))
    headers["Idempotency-Key"] = "tf1.202607.CQkJCQkJCQkJCQkJCQkJCQ"
    if cookie:
        headers["Cookie"] = cookie
    return headers, body


def test_missing_cookie_returns_428_without_acquiring(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ENV", "development")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_CALLER_SECRET", "s" * 32)
    monkeypatch.setattr(
        "trustforge.formal_run_runtime.formal_run_coordinator",
        lambda: (_ for _ in ()).throw(AssertionError("must not acquire")),
    )
    headers, body = _request_headers()
    code, text, response_headers = web._handle_api_formal_analysis_question(
        headers, io.BytesIO(body), "127.0.0.1"
    )
    assert code == 428
    assert json.loads(text)["error"]["code"] == "caller_scope_required"
    assert response_headers["Set-Cookie"].startswith("tf_formal_scope=tfcs1.")
    assert "HttpOnly" in response_headers["Set-Cookie"]
    assert "Secure" not in response_headers["Set-Cookie"]


def test_production_cookie_is_secure_and_old_rotation_key_validates(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_SCOPE_ACTIVE_KEY_ID", "old")
    monkeypatch.setenv(
        "TRUSTFORGE_FORMAL_SCOPE_SECRETS",
        json.dumps({"old": "o" * 32, "new": "n" * 32}),
    )
    scope, issued = web._formal_scope_cookie(Message())
    assert scope is None
    assert issued.startswith("__Host-tf_formal_scope=")
    assert "; Secure" in issued
    cookie = issued.split(";", 1)[0]
    monkeypatch.setenv("TRUSTFORGE_FORMAL_SCOPE_ACTIVE_KEY_ID", "new")
    headers = Message()
    headers["Cookie"] = cookie
    scope, replacement = web._formal_scope_cookie(headers)
    assert scope.startswith("browser:")
    assert "old" not in scope
    assert replacement is None


def test_dynamodb_runtime_requires_dedicated_scope_keyring_and_secure_cookie(
    monkeypatch,
):
    monkeypatch.delenv("TRUSTFORGE_ENV", raising=False)
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_CALLER_SECRET", "c" * 32)
    monkeypatch.delenv("TRUSTFORGE_FORMAL_SCOPE_SECRETS", raising=False)
    with pytest.raises(RuntimeError, match="keyring is unavailable"):
        web._formal_scope_cookie(Message())

    monkeypatch.setenv(
        "TRUSTFORGE_FORMAL_SCOPE_SECRETS",
        json.dumps({"scope-v1": "s" * 32}),
    )
    scope, issued = web._formal_scope_cookie(Message())
    assert scope is None
    assert issued.startswith("__Host-tf_formal_scope=")
    assert "; Secure" in issued
