from __future__ import annotations

from io import BytesIO
import json

import pytest

from trustforge import web
from trustforge.analysis_flow import MultiAngleBudgetError, MultiAngleCapacityError
from trustforge import rate_limit_store
from tests.test_rate_limit_store import _FakeDynamoDBTable, _make_store


@pytest.fixture(autouse=True)
def _allow_shared_analysis_write_rate_limit(monkeypatch):
    monkeypatch.setattr(
        web.rate_limit_store,
        "try_increment",
        lambda *_args, **_kwargs: True,
    )


def _post(body: dict) -> tuple[int, dict]:
    raw = json.dumps(body).encode()
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": str(len(raw)), "Idempotency-Key": "test-key"},
        BytesIO(raw),
        "127.0.0.1",
    )
    return code, json.loads(payload)


def test_multi_angle_post_success_envelope(monkeypatch):
    class Flow:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit_multi_angle(
            self, coin, question, *, locale, caller_id, idempotency_key, admission_check
        ):
            assert coin == "BTC"
            assert question
            assert locale == "zh-Hant"
            assert caller_id == "127.0.0.1"
            assert idempotency_key == "test-key"
            admission_check()
            return {"coin": coin, "snapshot_id": "snap-1", "job_ids": {"risk": "job-1"}}

    monkeypatch.setattr("trustforge.analysis_flow.AnalysisFlow", Flow)
    code, payload = _post({"coin": "BTC"})
    assert code == 200
    assert payload["ok"] is True
    assert payload["data"]["snapshot_id"] == "snap-1"


def test_multi_angle_post_budget_error_is_conflict(monkeypatch):
    class Flow:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit_multi_angle(self, *_args, **_kwargs):
            raise MultiAngleBudgetError("insufficient")

    monkeypatch.setattr("trustforge.analysis_flow.AnalysisFlow", Flow)
    code, payload = _post({"coin": "BTC"})
    assert code == 409
    assert payload["error"]["code"] == "multi_angle_budget_unavailable"


def test_multi_angle_post_capacity_error_is_retryable(monkeypatch):
    class Flow:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit_multi_angle(self, *_args, **_kwargs):
            raise MultiAngleCapacityError("full")

    monkeypatch.setattr("trustforge.analysis_flow.AnalysisFlow", Flow)
    code, payload = _post({"coin": "BTC"})
    assert code == 503
    assert payload["error"]["code"] == "multi_angle_queue_unavailable"


def test_multi_angle_post_authority_failure_is_503(monkeypatch):
    from trustforge.analysis_flow import MultiAngleAuthorityError

    class FakeFlow:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit_multi_angle(self, *_args, **_kwargs):
            raise MultiAngleAuthorityError("ddb unavailable")

    monkeypatch.setattr("trustforge.analysis_flow.AnalysisFlow", FakeFlow)
    code, payload = _post({"coin": "BTC", "question": "test"})
    assert code == 503
    assert payload["error"]["code"] == "multi_angle_authority_unavailable"


def test_multi_angle_post_rejects_invalid_json():
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": "1", "Idempotency-Key": "test-key"},
        BytesIO(b"{"),
        "127.0.0.1",
    )
    assert code == 400
    assert json.loads(payload)["error"]["code"] == "invalid_json"


def test_multi_angle_post_requires_idempotency_key():
    raw = json.dumps({"coin": "BTC"}).encode()
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": str(len(raw))}, BytesIO(raw), "127.0.0.1"
    )
    assert code == 400
    assert json.loads(payload)["error"]["code"] == "missing_idempotency_key"


def test_multi_angle_post_rejects_oversized_body_before_reading():
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": "4097", "Idempotency-Key": "test-key"},
        BytesIO(b""),
        "127.0.0.1",
    )
    assert code == 413
    assert json.loads(payload)["error"]["code"] == "payload_too_large"


def test_multi_angle_post_rejects_non_object_json():
    raw = b"[]"
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": str(len(raw)), "Idempotency-Key": "test-key"},
        BytesIO(raw),
        "127.0.0.1",
    )
    assert code == 400
    assert json.loads(payload)["error"]["code"] == "invalid_json"


def test_multi_angle_post_shared_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(
        web.rate_limit_store,
        "try_increment",
        lambda *_args, **_kwargs: False,
    )
    code, payload = _post({"coin": "BTC"})
    assert code == 429
    assert payload["error"]["code"] == "rate_limited"


def test_multi_angle_write_rate_limit_is_shared_across_instances(monkeypatch):
    table = _FakeDynamoDBTable()
    instance_a = _make_store(table)
    instance_b = _make_store(table)
    client_ip = "203.0.113.44"

    monkeypatch.setattr(web, "_STATUS_RATE_MAX", 2)
    monkeypatch.setattr(
        rate_limit_store,
        "try_increment",
        lambda *args, **kwargs: rate_limit_store._default_store_instance.try_increment(
            *args, **kwargs
        ),
    )
    monkeypatch.setattr(rate_limit_store, "_default_store_instance", instance_a)
    web._check_analysis_write_rate_limit(client_ip)

    monkeypatch.setattr(rate_limit_store, "_default_store_instance", instance_b)
    web._check_analysis_write_rate_limit(client_ip)
    with pytest.raises(web.TooManyRequests):
        web._check_analysis_write_rate_limit(client_ip)


def test_multi_angle_write_rate_limit_backend_failure_is_fail_closed(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise rate_limit_store.RateLimitBackendError("unavailable")

    monkeypatch.setattr(rate_limit_store, "try_increment", unavailable)
    with pytest.raises(web.TooManyRequests, match="保護暫時無法確認"):
        web._check_analysis_write_rate_limit("203.0.113.45")
