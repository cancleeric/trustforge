from __future__ import annotations

from io import BytesIO
import json

from trustforge import web
from trustforge.analysis_flow import MultiAngleBudgetError, MultiAngleCapacityError


def _post(body: dict) -> tuple[int, dict]:
    raw = json.dumps(body).encode()
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": str(len(raw))}, BytesIO(raw), "127.0.0.1"
    )
    return code, json.loads(payload)


def test_multi_angle_post_success_envelope(monkeypatch):
    class Flow:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit_multi_angle(self, coin, question, *, locale):
            assert coin == "BTC"
            assert question
            assert locale == "zh-Hant"
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


def test_multi_angle_post_rejects_invalid_json():
    code, payload = web._handle_api_multi_angle_post(
        {"Content-Length": "1"}, BytesIO(b"{"), "127.0.0.1"
    )
    assert code == 400
    assert json.loads(payload)["error"]["code"] == "invalid_json"
