from __future__ import annotations

import json

from trustforge.web import _handle_api_agentcore_status


def test_agentcore_status_api_is_non_sensitive(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json")
    )
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:must-not-leak")

    code, body = _handle_api_agentcore_status()

    assert code == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["data"]["runtime_configured"] is True
    assert "arn:must-not-leak" not in body
