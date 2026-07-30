from __future__ import annotations

import json
from pathlib import Path

from trustforge import web


def _payload(body: str) -> dict:
    return json.loads(body)


def test_training_status_uses_configured_authoritative_directory(
    monkeypatch, tmp_path: Path
) -> None:
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    (training_dir / "btc.jsonl").write_text(
        "\n".join(
            [
                '{"direction":"bullish"}',
                '{"direction":"不明"}',
                '{"direction":"bearish"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRUSTFORGE_TRAINING_DATA_DIR", str(training_dir))

    status, body = web._handle_api_training_status()

    assert status == 200
    data = _payload(body)["data"]["training_data"]
    assert data["total_records"] == 3
    assert data["has_direction"] == 2
    assert data["per_coin"]["BTC"] == {"total": 3, "has_direction": 2}


def test_training_status_missing_configured_directory_fails_visibly(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "TRUSTFORGE_TRAINING_DATA_DIR", str(tmp_path / "does-not-exist")
    )

    status, body = web._handle_api_training_status()

    assert status == 503
    assert _payload(body)["error"]["code"] == "training_data_unavailable"


def test_training_status_rejects_relative_configured_directory(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTFORGE_TRAINING_DATA_DIR", "data/training")

    status, body = web._handle_api_training_status()

    assert status == 503
    assert _payload(body)["error"]["code"] == "training_data_unavailable"


def test_deployment_reconciles_production_training_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    deploy = (root / "deploy" / "deploy_ec2.sh").read_text(encoding="utf-8")
    activate = (root / "deploy" / "activate_release.sh").read_text(encoding="utf-8")

    expected = "/opt/trustforge/data/training"
    assert f"TRUSTFORGE_TRAINING_DATA_DIR:-{expected}" in deploy
    assert "Environment=TRUSTFORGE_TRAINING_DATA_DIR=" in deploy
    assert f"TRUSTFORGE_TRAINING_DATA_DIR:-{expected}" in activate
    assert "TRAINING_RECONCILE_COMMAND" in activate
