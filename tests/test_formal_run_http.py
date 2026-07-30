import io
import json
from email.message import Message

import pytest

from trustforge import web
from trustforge.formal_run_coordinator import FormalRunOutcome
from trustforge.formal_run_idempotency import parse_idempotency_key


@pytest.fixture(autouse=True)
def _stable_browser_scope(monkeypatch):
    monkeypatch.setattr(
        web,
        "_formal_scope_cookie",
        lambda _headers: ("browser:scope-v1:stable", None),
    )


class Coordinator:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        parse_idempotency_key(kwargs["idempotency_keys"])
        return self.outcome


def headers(*keys):
    value = Message()
    body = json.dumps(
        {
            "coin": "BTC",
            "mode": "risk",
            "question": "Assess risk",
            "locale": "zh-Hant",
            "fresh": False,
        }
    ).encode()
    value["Content-Length"] = str(len(body))
    value["Content-Type"] = "application/json"
    for key in keys:
        value["Idempotency-Key"] = key
    return value, body


def test_formal_route_rejects_duplicate_idempotency_header(monkeypatch):
    coordinator = Coordinator(FormalRunOutcome(202, {}))
    monkeypatch.setattr(
        "trustforge.formal_run_runtime.formal_run_coordinator",
        lambda: coordinator,
    )
    request_headers, body = headers("one", "two")
    code, text, response_headers = web._handle_api_formal_analysis_question(
        request_headers, io.BytesIO(body), "127.0.0.1"
    )
    assert code == 400
    assert json.loads(text)["error"]["code"] == "bad_request"
    assert response_headers == {}
    assert len(coordinator.calls) == 1


def test_formal_route_uses_trusted_scope_and_replay_header(monkeypatch):
    coordinator = Coordinator(
        FormalRunOutcome(
            202,
            {
                "receipt_id": "frc_1",
                "question_id": "q_1",
                "job_id": "job_1",
            },
            replayed=True,
        )
    )
    monkeypatch.setattr(
        "trustforge.formal_run_runtime.formal_run_coordinator",
        lambda: coordinator,
    )
    request_headers, body = headers("tf1.202607.CQkJCQkJCQkJCQkJCQkJCQ")
    code, text, response_headers = web._handle_api_formal_analysis_question(
        request_headers, io.BytesIO(body), "127.0.0.1"
    )
    assert code == 202
    assert json.loads(text)["ok"] is True
    assert response_headers == {"Idempotency-Replayed": "true"}
    assert coordinator.calls[0]["caller_scope"] == "browser:scope-v1:stable"


def test_nonreplay_conflict_does_not_emit_replay_header(monkeypatch):
    coordinator = Coordinator(
        FormalRunOutcome(
            409,
            {
                "ok": False,
                "data": None,
                "error": {"code": "idempotency_conflict"},
            },
        )
    )
    monkeypatch.setattr(
        "trustforge.formal_run_runtime.formal_run_coordinator",
        lambda: coordinator,
    )
    request_headers, body = headers("tf1.202607.CQkJCQkJCQkJCQkJCQkJCQ")
    code, _text, response_headers = web._handle_api_formal_analysis_question(
        request_headers, io.BytesIO(body), "127.0.0.1"
    )
    assert code == 409
    assert response_headers == {}
