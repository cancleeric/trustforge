"""Tests for the Hermes backfill worker (issue #291)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.backfill import (
    BackfillControl,
    BackfillWorker,
    backfill_enabled,
    set_backfill_enabled,
)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Block real HTTP calls in backfill tests.

    ``BackfillWorker._load_historical_sources`` fetches full Fear & Greed +
    blockchain.com chart histories from live APIs with 30 s timeouts.  Each
    ``run_batch`` day triggers these calls, making the suite the slowest
    segment (~75 s of the full run).  The worker degrades gracefully when
    they fail (snapshot still has local OHLCV data), so stubbing ``fetch_url``
    to raise immediately makes tests deterministic and fast without changing
    the assertions under test.
    """

    def _fail(*_args, **_kwargs):
        raise OSError("network disabled in backfill tests")

    monkeypatch.setattr(
        "trustforge.ingestion.safe_fetch.fetch_url", _fail,
    )


@pytest.fixture
def tmp_env(tmp_path):
    """Provide isolated paths for backfill state and DB."""
    state_file = tmp_path / "backfill-control.json"
    db_file = tmp_path / "backfill.sqlite3"
    env = {
        "TRUSTFORGE_BACKFILL_STATE_PATH": str(state_file),
        "TRUSTFORGE_BACKFILL_ENABLED": "",  # clear env override
        "TRUSTFORGE_HOME": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=False):
        yield {"state": state_file, "db": db_file, "tmp": tmp_path}


# ─── 啟停控制 ─────────────────────────────────────────────────────────────


class TestBackfillControl:
    def test_default_is_disabled(self, tmp_env):
        """預設 backfill 是關閉的。"""
        ctrl = backfill_enabled()
        assert ctrl.enabled is False
        assert ctrl.source == "default"

    def test_env_overrides_all(self, tmp_env):
        """環境變數優先於 state file。"""
        # Write state file as enabled
        set_backfill_enabled(True, reason="test")
        # But env says disabled
        with patch.dict(os.environ, {"TRUSTFORGE_BACKFILL_ENABLED": "off"}):
            ctrl = backfill_enabled()
            assert ctrl.enabled is False
            assert ctrl.source == "env"

    def test_state_file_toggle(self, tmp_env):
        """State file 可以開關 backfill。"""
        ctrl = set_backfill_enabled(True, reason="test start")
        assert ctrl.enabled is True
        assert ctrl.source == "state_file"

        ctrl = set_backfill_enabled(False, reason="test stop")
        assert ctrl.enabled is False
        assert ctrl.source == "state_file"

    def test_env_enabled(self, tmp_env):
        """環境變數可以啟用 backfill。"""
        with patch.dict(os.environ, {"TRUSTFORGE_BACKFILL_ENABLED": "1"}):
            ctrl = backfill_enabled()
            assert ctrl.enabled is True
            assert ctrl.source == "env"


# ─── 進度持久化與斷點續跑 ──────────────────────────────────────────────────


class TestBackfillProgress:
    def test_seed_and_progress(self, tmp_env):
        """Seed tasks 正確寫入，progress 正確統計。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-01-01",
            end_date="2024-01-05",
            batch_size=10,
        )
        seeded = worker.seed_tasks()
        assert seeded == 5  # 5 天

        progress = worker.progress()
        assert "BTC" in progress
        p = progress["BTC"]
        assert p.total_days == 5
        assert p.completed_days == 0
        assert p.state == "paused"  # seeded 但沒在跑
        worker.close()

    def test_breakpoint_resume(self, tmp_env):
        """斷點續跑：completed 不重跑。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-06-01",
            end_date="2024-06-05",
            batch_size=3,
        )
        worker.seed_tasks()

        # 跑 3 天
        results = worker.run_batch()
        assert len(results) == 3
        assert all(r.state == "completed" for r in results)

        # 再跑一批 → 只剩 2 天
        results2 = worker.run_batch()
        assert len(results2) == 2
        assert all(r.state == "completed" for r in results2)

        # 再跑 → 空的
        results3 = worker.run_batch()
        assert len(results3) == 0

        progress = worker.progress()
        assert progress["BTC"].completed_days == 5
        assert progress["BTC"].state == "completed"
        worker.close()

    def test_completed_days_write_portable_training_jsonl(self, tmp_env):
        """成功回填會同步匯出可搬遷的 JSON Lines 訓練資料。"""
        set_backfill_enabled(True, reason="test")
        training_dir = tmp_env["tmp"] / "training-data"
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-06-01",
            end_date="2024-06-01",
            batch_size=1,
            training_data_dir=training_dir,
        )
        worker.seed_tasks()

        results = worker.run_batch()

        assert len(results) == 1
        assert results[0].state == "completed"
        rows = [
            json.loads(line)
            for line in (training_dir / "btc-backfill.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 1
        assert rows[0]["coin"] == "BTC"
        assert rows[0]["date"] == "2024-06-01"
        assert rows[0]["archive_type"] == "backfilled_archive"
        assert rows[0]["snapshot_id"] == "backfill-btc-2024-06-01"
        assert rows[0]["document_count"] > 0
        assert rows[0]["sources"]
        worker.close()

    def test_reset_failed(self, tmp_env):
        """reset_failed 把 failed 重設為 pending。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-01-01",
            end_date="2024-01-03",
            batch_size=10,
        )
        worker.seed_tasks()

        # 手動把一筆設為 failed
        conn = worker._get_conn()
        conn.execute(
            "UPDATE backfill_tasks SET state='failed' WHERE date_str='2024-01-02'",
        )

        progress = worker.progress()
        assert progress["BTC"].failed_days == 1

        count = worker.reset_failed()
        assert count == 1

        progress = worker.progress()
        assert progress["BTC"].failed_days == 0
        worker.close()


# ─── Batch 控制 ────────────────────────────────────────────────────────────


class TestBatchControl:
    def test_batch_size_respected(self, tmp_env):
        """batch_size 限制一次處理的天數。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-03-01",
            end_date="2024-03-10",
            batch_size=4,
        )
        worker.seed_tasks()
        results = worker.run_batch()
        assert len(results) == 4
        worker.close()

    def test_disabled_stops_batch(self, tmp_env):
        """backfill 被停用時 batch 提前結束。"""
        # Start enabled, seed tasks
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-04-01",
            end_date="2024-04-10",
            batch_size=10,
        )
        worker.seed_tasks()

        # Now disable
        set_backfill_enabled(False, reason="test stop")
        results = worker.run_batch()
        # Should stop immediately (0 results because check is before processing)
        assert len(results) == 0
        worker.close()


# ─── 日期邊界 ──────────────────────────────────────────────────────────────


class TestDateBoundary:
    def test_start_after_end_yields_zero(self, tmp_env):
        """start > end 時沒有任務。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2025-01-10",
            end_date="2025-01-01",
            batch_size=10,
        )
        seeded = worker.seed_tasks()
        assert seeded == 0
        worker.close()

    def test_plan_matches_ohlcv_data(self, tmp_env):
        """plan() 只計算有 OHLCV 資料的日期。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            batch_size=10,
        )
        plan = worker.plan()
        # 一月有 31 天，全部都應該有 OHLCV 資料
        assert plan["BTC"] == 31
        worker.close()


# ─── Status API ────────────────────────────────────────────────────────────


class TestStatusAPI:
    def test_status_shape(self, tmp_env):
        """status() 回傳正確的 API schema。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC", "ETH"],
            start_date="2024-07-01",
            end_date="2024-07-05",
            batch_size=10,
        )
        worker.seed_tasks()
        status = worker.status()

        assert "enabled" in status
        assert "source" in status
        assert "is_running" in status
        assert "coins" in status
        assert "date_range" in status
        assert "total_days" in status
        assert "total_completed" in status
        assert "total_remaining" in status
        assert "progress_pct" in status
        assert "per_coin" in status
        assert "BTC" in status["per_coin"]
        assert "ETH" in status["per_coin"]
        worker.close()


# ─── 持久化到 trust_snapshot_history ──────────────────────────────────────────


class TestBackfillPersistToTrustHistory:
    """驗證 backfill 的 replay 結果正確寫入 trust_snapshot_history_key，
    讓 get_trust_history() 能讀到。"""

    def test_completed_days_readable_by_get_trust_history(self, tmp_env):
        """回填完成的天數可透過 get_trust_history 讀到。"""
        from trustforge.ingestion.cache import get_cache_backend, get_trust_history

        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-06-01",
            end_date="2024-06-03",
            batch_size=10,
        )
        worker.seed_tasks()
        results = worker.run_batch()

        # 確認有完成的天
        completed = [r for r in results if r.state == "completed"]
        assert len(completed) >= 1

        # 透過 get_trust_history 讀取
        backend = get_cache_backend()
        history = get_trust_history("BTC", 30, backend, end_date="2024-06-30")

        # 應該讀到至少跟 completed 同樣數量的 entries
        assert len(history) >= len(completed)

        # 驗證每筆都有必要欄位
        for entry in history:
            assert "trust_score" in entry
            assert "direction" in entry
            assert "coin" in entry
            assert entry["coin"] == "BTC"
            assert "date" in entry  # get_trust_history 自動補的

    def test_backfill_entries_have_archive_type(self, tmp_env):
        """回填寫入的 snapshot 帶有 archive_type=backfilled_archive。"""
        from trustforge.ingestion.cache import get_cache_backend, get_trust_history

        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["ETH"],
            start_date="2024-05-01",
            end_date="2024-05-02",
            batch_size=10,
        )
        worker.seed_tasks()
        results = worker.run_batch()
        completed = [r for r in results if r.state == "completed"]
        assert len(completed) >= 1

        backend = get_cache_backend()
        history = get_trust_history("ETH", 60, backend, end_date="2024-05-31")
        assert len(history) >= 1

        for entry in history:
            assert entry.get("archive_type") == "backfilled_archive"

    def test_backfill_format_matches_fetch_scheduler(self, tmp_env):
        """回填寫入的格式與 fetch_scheduler _snapshot_dict 一致。"""
        from trustforge.ingestion.cache import get_cache_backend, get_trust_history

        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-07-01",
            end_date="2024-07-01",
            batch_size=1,
        )
        worker.seed_tasks()
        results = worker.run_batch()
        assert results[0].state == "completed"

        backend = get_cache_backend()
        history = get_trust_history("BTC", 10, backend, end_date="2024-07-05")
        assert len(history) == 1

        snap = history[0]
        # 必備欄位（對齊 _snapshot_dict）
        assert isinstance(snap["trust_score"], (int, float))
        assert isinstance(snap["direction"], str)
        assert isinstance(snap["calibrated_confidence"], (int, float))
        assert "decision_state" in snap
        assert "generated_at" in snap
        # backfill 特有欄位
        assert snap["archive_type"] == "backfilled_archive"
        assert "snapshot_epoch" in snap


# ─── Issue #328: mode=live, sample, training data ─────────────────────────────


class TestBackfillModeSample:
    """驗證 Issue #328 新增的 mode 與 sample 參數。"""

    def test_default_mode_is_offline(self, tmp_env):
        """預設 mode 為 offline。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-01-01",
            end_date="2024-01-05",
        )
        assert worker.mode == "offline"
        assert worker.sample is None
        worker.close()

    def test_invalid_mode_raises(self, tmp_env):
        """非法 mode 值應 raise ValueError。"""
        with pytest.raises(ValueError, match="mode must be"):
            BackfillWorker(
                db_path=tmp_env["db"],
                coins=["BTC"],
                start_date="2024-01-01",
                end_date="2024-01-05",
                mode="invalid",
            )

    def test_sample_reduces_seeded_tasks(self, tmp_env):
        """sample 參數限制 seed_tasks 產出的任務數。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            sample=10,
        )
        seeded = worker.seed_tasks()
        # 2024 全年有 366 天，sample=10 只寫 10 筆
        assert seeded == 10
        worker.close()

    def test_sample_none_seeds_all(self, tmp_env):
        """sample=None 寫入全部天數。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-06-01",
            end_date="2024-06-10",
            sample=None,
        )
        seeded = worker.seed_tasks()
        assert seeded == 10
        worker.close()

    def test_sample_larger_than_available_seeds_all(self, tmp_env):
        """sample > 可用天數時，寫入全部。"""
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-06-01",
            end_date="2024-06-05",
            sample=999,
        )
        seeded = worker.seed_tasks()
        assert seeded == 5
        worker.close()


class TestBackfillTrainingData:
    """驗證 Issue #328 training data JSONL 持久化。"""

    def test_training_data_created_on_backfill(self, tmp_env):
        """回填完成後應建立 training-data JSONL 檔案。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["BTC"],
            start_date="2024-07-01",
            end_date="2024-07-02",
            batch_size=5,
        )
        worker.seed_tasks()
        results = worker.run_batch()
        completed = [r for r in results if r.state == "completed"]
        assert len(completed) >= 1

        # 確認 training data 檔案存在
        training_path = Path(os.environ.get(
            "TRUSTFORGE_HOME",
            str(Path(__file__).resolve().parents[1]),
        )) / "data" / "training" / "BTC.jsonl"
        assert training_path.exists()

        # 讀取確認格式正確
        lines = training_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= len(completed)

        for line in lines:
            record = json.loads(line)
            assert "date" in record
            assert "coin" in record
            assert record["coin"] == "BTC"
            assert "direction" in record
            assert "trust_score" in record
            assert "confidence" in record
            assert "evidence_count" in record
            assert "sources" in record
            assert isinstance(record["sources"], list)
            assert "model_id" in record
            assert "generated_at" in record

        worker.close()

    def test_training_data_offline_model_id_is_none(self, tmp_env):
        """offline mode 的 model_id 應為 None。"""
        set_backfill_enabled(True, reason="test")
        worker = BackfillWorker(
            db_path=tmp_env["db"],
            coins=["ETH"],
            start_date="2024-08-01",
            end_date="2024-08-01",
            batch_size=1,
            mode="offline",
        )
        worker.seed_tasks()
        worker.run_batch()

        training_path = Path(os.environ.get(
            "TRUSTFORGE_HOME",
            str(Path(__file__).resolve().parents[1]),
        )) / "data" / "training" / "ETH.jsonl"
        assert training_path.exists()

        line = training_path.read_text(encoding="utf-8").strip().split("\n")[0]
        record = json.loads(line)
        assert record["model_id"] is None
        worker.close()


# ─── Issue #355: 自動健康檢查 ─────────────────────────────────────────────────


@pytest.fixture
def isolated_env(tmp_path):
    """Provide fully isolated TRUSTFORGE_HOME for health check tests."""
    state_file = tmp_path / "backfill-control.json"
    db_file = tmp_path / "backfill.sqlite3"
    env = {
        "TRUSTFORGE_BACKFILL_STATE_PATH": str(state_file),
        "TRUSTFORGE_BACKFILL_ENABLED": "",
        "TRUSTFORGE_HOME": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=False):
        yield {"state": state_file, "db": db_file, "tmp": tmp_path}


class TestBatchHealthCheck:
    """驗證 Issue #355 自動健康檢查機制。"""

    def test_high_failure_rate_writes_anomaly(self, isolated_env):
        """失敗率 >10% 時寫入 anomaly-report.json。"""
        from trustforge.backfill import (
            BackfillDayResult,
            _check_batch_health,
            _anomaly_report_path,
        )

        # 模擬 10 個結果，2 個 failed (20%)
        results = [
            BackfillDayResult("BTC", f"2024-01-{i:02d}", "completed")
            for i in range(1, 9)
        ] + [
            BackfillDayResult("BTC", "2024-01-09", "failed", error="timeout"),
            BackfillDayResult("BTC", "2024-01-10", "failed", error="timeout"),
        ]

        anomalies = _check_batch_health(results)
        assert len(anomalies) == 1
        assert anomalies[0]["type"] == "high_failure_rate"
        assert anomalies[0]["failed_count"] == 2
        assert anomalies[0]["failure_rate"] > 0.10

        # 確認寫入檔案
        path = _anomaly_report_path()
        assert path.is_file()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["type"] == "high_failure_rate"

    def test_no_anomaly_for_normal_batch(self, isolated_env):
        """正常 batch（低失敗率）不寫入 anomaly。"""
        from trustforge.backfill import (
            BackfillDayResult,
            _check_batch_health,
            _anomaly_report_path,
        )

        # 10 個 completed，0 個 failed
        results = [
            BackfillDayResult("BTC", f"2024-01-{i:02d}", "completed")
            for i in range(1, 11)
        ]

        anomalies = _check_batch_health(results)
        assert len(anomalies) == 0

    def test_direction_bias_detected(self, isolated_env):
        """方向偏差 >95% 時寫入 anomaly。"""
        from trustforge.backfill import (
            BackfillDayResult,
            _check_batch_health,
            _anomaly_report_path,
        )

        # 需要在 training data 中建立對應的方向資料
        tmp = isolated_env["tmp"]
        training_dir = tmp / "data" / "training"
        training_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = training_dir / "BTC.jsonl"

        # 寫入 20 筆全部都是 bearish 的 training data
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for i in range(1, 21):
                record = {
                    "date": f"2024-03-{i:02d}",
                    "coin": "BTC",
                    "direction": "bearish",
                    "trust_score": 0.5,
                }
                f.write(json.dumps(record) + "\n")

        # 模擬 20 個 completed 結果
        results = [
            BackfillDayResult("BTC", f"2024-03-{i:02d}", "completed")
            for i in range(1, 21)
        ]

        anomalies = _check_batch_health(results)
        # 應該偵測到方向偏差
        direction_anomalies = [a for a in anomalies if a["type"] == "direction_bias"]
        assert len(direction_anomalies) == 1
        assert direction_anomalies[0]["dominant_direction"] == "bearish"
        assert direction_anomalies[0]["dominant_ratio"] > 0.95

    def test_empty_results_no_anomaly(self, isolated_env):
        """空結果不觸發 anomaly。"""
        from trustforge.backfill import _check_batch_health

        anomalies = _check_batch_health([])
        assert anomalies == []

    def test_read_recent_anomalies(self, isolated_env):
        """read_recent_anomalies 正確讀取最近 N 筆。"""
        from trustforge.backfill import (
            _anomaly_report_path,
            _write_anomaly,
            read_recent_anomalies,
        )

        # 寫入 7 筆
        for i in range(7):
            _write_anomaly({"type": "test", "index": i})

        # 讀取最近 5 筆
        recent = read_recent_anomalies(limit=5)
        assert len(recent) == 5
        # 應該是最後 5 筆
        assert recent[0]["index"] == 2
        assert recent[4]["index"] == 6

    def test_read_recent_anomalies_empty_file(self, isolated_env):
        """anomaly 檔案不存在時回傳空列表。"""
        from trustforge.backfill import read_recent_anomalies

        recent = read_recent_anomalies()
        assert recent == []
