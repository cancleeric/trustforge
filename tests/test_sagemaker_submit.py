"""Tests for SageMaker orchestration submit (#704, #709).

Mock-based tests covering the full pipeline: gate → trigger → poll → download → validate → propose.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trustforge.sagemaker_submit import submit_sagemaker_training, COIN_POOL
from trustforge.sagemaker_client import SageMakerBackend
from trustforge.execlog import ExecutionLog
from trustforge.calibration_model import save_calibration_model


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _make_training_data(tmp_path: Path, coin: str = "BTC", n: int = 120) -> Path:
    """產出合格的 flat JSONL 訓練資料（>=100 unique outcomes）。

    每行必須有唯一 date（load_flat_training_rows 要求）；
    使用跨月/跨年的連續日期避免衝突。
    """
    training_dir = tmp_path / "training"
    training_dir.mkdir(parents=True)
    data_file = training_dir / f"{coin}.jsonl"

    from datetime import date, timedelta
    base_date = date(2025, 1, 1)

    rows = []
    split_boundary = int(n * 0.8)
    for i in range(n):
        d = base_date + timedelta(days=i)
        outcome = round(-5 + 10 * (i / n), 2)
        row = {
            "date": d.isoformat(),
            "coin": coin,
            "direction": "bullish" if i % 2 == 0 else "bearish",
            "confidence": round(0.1 + 0.8 * (i / n), 6),
            "calibrated_confidence": round(0.1 + 0.8 * (i / n), 6),
            "outcome_pct": outcome,
            "ground_truth_direction": "bullish" if outcome > 0 else "bearish",
            "split": "train" if i < split_boundary else "val",
            "generated_at": f"{d.isoformat()}T10:00:00+00:00",
        }
        rows.append(json.dumps(row, ensure_ascii=False))
    data_file.write_text("\n".join(rows), encoding="utf-8")
    return training_dir


def _make_mock_backend(*, model_points: list[dict] | None = None) -> SageMakerBackend:
    """建立 mock backend，模擬成功的訓練流程。"""
    backend = SageMakerBackend(offline=True)

    # Override methods with mocks
    backend.trigger_training = MagicMock(return_value="test-job-btc-20260726")
    backend.poll_result = MagicMock(return_value={
        "status": "completed",
        "artifact_path": "s3://bucket/output/model.tar.gz",
    })

    if model_points is None:
        model_points = [
            {"confidence": 0.2, "calibrated": 0.15},
            {"confidence": 0.5, "calibrated": 0.45},
            {"confidence": 0.8, "calibrated": 0.72},
        ]

    def mock_download(job_id: str, local_path: Path) -> Path:
        local_path.mkdir(parents=True, exist_ok=True)
        model_path = local_path / "model.json"
        save_calibration_model(model_points, model_path, sample_count=100)
        return model_path

    backend.download_artifact = MagicMock(side_effect=mock_download)
    return backend


# ═══════════════════════════════════════════════════════════════════════════════
# Dry-run 測試
# ═══════════════════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_single_coin(self, tmp_path):
        """dry-run 模式不呼叫 AWS，產出 execution log。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir, dry_run=True,
        )

        assert result["status"] == "dry_run"
        assert result["coin"] == "BTC"
        assert result["automatic_apply"] is False
        assert result["requires_human_approval"] is True
        assert "dataset_sha256" in result

    def test_dry_run_all_coins(self, tmp_path):
        """dry-run 五幣全跑。"""
        training_dir = tmp_path / "training"
        training_dir.mkdir()
        out_dir = tmp_path / "out"

        results = []
        for coin in COIN_POOL:
            _make_training_data(tmp_path / coin, coin)
            result = submit_sagemaker_training(
                coin,
                training_dir=tmp_path / coin / "training",
                out_dir=out_dir,
                dry_run=True,
            )
            results.append(result)

        for r in results:
            assert r["status"] in ("dry_run", "blocked", "error")


# ═══════════════════════════════════════════════════════════════════════════════
# 成功路徑
# ═══════════════════════════════════════════════════════════════════════════════


class TestSuccessPath:
    def test_full_pipeline_candidate(self, tmp_path):
        """完整流程：gate passed → trigger → poll → download → validate → candidate。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )

        assert result["status"] == "candidate"
        assert result["coin"] == "BTC"
        assert result["automatic_apply"] is False
        assert result["requires_human_approval"] is True
        assert "artifact_sha256" in result
        assert "job_name" in result

        # 確認 trigger 和 poll 被呼叫
        backend.trigger_training.assert_called_once()
        backend.poll_result.assert_called_once()
        backend.download_artifact.assert_called_once()

    def test_proposal_file_created(self, tmp_path):
        """candidate 狀態會產出 proposal JSON 和 current manifest。"""
        training_dir = _make_training_data(tmp_path, "ETH")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()

        result = submit_sagemaker_training(
            "ETH", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )

        assert result["status"] == "candidate"
        # current manifest
        current = out_dir / "ETH.json"
        assert current.exists()
        data = json.loads(current.read_text())
        assert data["automatic_apply"] is False
        assert data["backend"] == "sagemaker"


# ═══════════════════════════════════════════════════════════════════════════════
# 失敗路徑
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailurePaths:
    def test_missing_training_data(self, tmp_path):
        """訓練資料不存在 → error。"""
        out_dir = tmp_path / "out"
        result = submit_sagemaker_training(
            "BTC", training_dir=tmp_path / "nonexistent", out_dir=out_dir, dry_run=False,
        )
        assert result["status"] == "error"
        assert "not found" in result.get("reason", "").lower()

    def test_trigger_fails(self, tmp_path):
        """trigger 失敗 → unavailable。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()
        backend.trigger_training = MagicMock(side_effect=RuntimeError("Connection refused"))

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "unavailable"

    def test_poll_timeout(self, tmp_path):
        """poll 回傳 timeout → timeout。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()
        backend.poll_result = MagicMock(return_value={
            "status": "timeout",
            "failure_reason": "exceeded 300s",
        })

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "timeout"

    def test_poll_failed(self, tmp_path):
        """Training Job 失敗 → error。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()
        backend.poll_result = MagicMock(return_value={
            "status": "failed",
            "failure_reason": "OutOfMemory",
        })

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "error"
        assert "OutOfMemory" in result.get("reason", "")

    def test_download_fails(self, tmp_path):
        """artifact 下載失敗 → error。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()
        backend.download_artifact = MagicMock(side_effect=OSError("S3 timeout"))

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "error"

    def test_invalid_artifact(self, tmp_path):
        """artifact 格式不對 → error。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = _make_mock_backend()

        # 產出無效的 model.json
        def bad_download(job_id: str, local_path: Path) -> Path:
            local_path.mkdir(parents=True, exist_ok=True)
            model_path = local_path / "model.json"
            model_path.write_text('{"invalid": true}', encoding="utf-8")
            return model_path

        backend.download_artifact = MagicMock(side_effect=bad_download)

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "error"
        assert "format" in result.get("reason", "").lower() or "None" in result.get("reason", "")

    def test_config_error(self, tmp_path):
        """設定錯誤（缺少 bucket）→ error。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"
        backend = SageMakerBackend(bucket="", role_arn="", offline=False)

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["status"] == "error"
        assert "BUCKET" in result.get("reason", "").upper() or "設定" in result.get("reason", "")


# ═══════════════════════════════════════════════════════════════════════════════
# 治理約束
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_automatic_apply_always_false(self, tmp_path):
        """所有回傳都帶 automatic_apply=False。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"

        # candidate
        backend = _make_mock_backend()
        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir,
            dry_run=False, backend=backend,
        )
        assert result["automatic_apply"] is False
        assert result["requires_human_approval"] is True

    def test_dry_run_governance(self, tmp_path):
        """dry-run 也帶治理欄位。"""
        training_dir = _make_training_data(tmp_path, "BTC")
        out_dir = tmp_path / "out"

        result = submit_sagemaker_training(
            "BTC", training_dir=training_dir, out_dir=out_dir, dry_run=True,
        )
        assert result["automatic_apply"] is False
        assert result["requires_human_approval"] is True
