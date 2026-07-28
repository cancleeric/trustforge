from __future__ import annotations

from unittest import mock

import pytest

from trustforge.agent.agentcore_event import changed_coins, run_changed_analyses


def test_changed_coins_detects_only_newer_inputs(tmp_path):
    btc = tmp_path / "btc.json"
    eth = tmp_path / "eth.json"
    btc.write_text("btc")
    eth.write_text("eth")

    changed, snapshot = changed_coins(
        {"BTC": btc, "ETH": eth},
        {"BTC": btc.stat().st_mtime, "ETH": 0.0},
    )

    assert changed == ["ETH"]
    assert snapshot["BTC"] == btc.stat().st_mtime


def test_run_changed_analyses_is_one_shot(tmp_path):
    btc = tmp_path / "btc.json"
    btc.write_text("btc")
    invoke = mock.Mock(
        return_value={"run_id": "1", "status": "succeeded", "output": {}}
    )

    receipt = run_changed_analyses(
        {"BTC": btc},
        invoke=invoke,
        status=lambda: {"state": "configured"},
    )

    assert receipt["changed_coins"] == ["BTC"]
    assert receipt["results"][0]["result"]["status"] == "succeeded"
    invoke.assert_called_once_with(
        "hermes",
        "分析 BTC 最新多來源市場訊號",
        runtime_payload={
            "coin": "BTC",
            "query": "分析 BTC 最新多來源市場訊號",
            "question_type": "multi_source",
            "data_mode": "live",
            "llm_mode": "bedrock",
        },
    )


def test_run_changed_analyses_rejects_builtin_provider(tmp_path):
    btc = tmp_path / "btc.json"
    btc.write_text("btc")
    invoke = mock.Mock()

    with pytest.raises(RuntimeError, match="must be selected"):
        run_changed_analyses(
            {"BTC": btc},
            invoke=invoke,
            status=lambda: {"state": "inactive"},
        )

    invoke.assert_not_called()
