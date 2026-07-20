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


@pytest.fixture
def tmp_env(tmp_path):
    """Provide isolated paths for backfill state and DB."""
    state_file = tmp_path / "backfill-control.json"
    db_file = tmp_path / "backfill.sqlite3"
    env = {
        "TRUSTFORGE_BACKFILL_STATE_PATH": str(state_file),
        "TRUSTFORGE_BACKFILL_ENABLED": "",  # clear env override
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
