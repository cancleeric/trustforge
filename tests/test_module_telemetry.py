"""Tests for module_telemetry.py (issue #382)."""
from __future__ import annotations

import os
import tempfile
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test gets a fresh singleton."""
    from trustforge.module_telemetry import ModuleTelemetry
    ModuleTelemetry.reset_instance()
    yield
    ModuleTelemetry.reset_instance()


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite path."""
    return str(tmp_path / "test-telemetry.sqlite3")


class TestRecordInvocation:
    """record_invocation() should persist to SQLite."""

    def test_basic_record_and_get(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("trust.scoring", 42.5, "success")
        # Wait for background writer
        time.sleep(0.5)

        rec = tele.get_telemetry("trust.scoring")
        assert rec is not None
        assert rec.module_id == "trust.scoring"
        assert rec.invocation_count == 1
        assert rec.last_result == "success"
        assert rec.avg_latency_ms == pytest.approx(42.5, rel=0.01)
        assert rec.last_latency_ms == pytest.approx(42.5, rel=0.01)
        tele.shutdown()

    def test_multiple_records_accumulate(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("agent.build_report", 100.0, "success")
        tele.record_invocation("agent.build_report", 200.0, "success")
        tele.record_invocation("agent.build_report", 300.0, "failure")
        time.sleep(0.5)

        rec = tele.get_telemetry("agent.build_report")
        assert rec is not None
        assert rec.invocation_count == 3
        assert rec.last_result == "failure"
        # avg = (100 + 200 + 300) / 3 = 200
        assert rec.avg_latency_ms == pytest.approx(200.0, rel=0.01)
        assert rec.last_latency_ms == pytest.approx(300.0, rel=0.01)
        tele.shutdown()

    def test_get_nonexistent_returns_none(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        assert tele.get_telemetry("nonexistent.module") is None
        tele.shutdown()

    def test_get_all_telemetry(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("mod.a", 10.0, "success")
        tele.record_invocation("mod.b", 20.0, "degraded")
        time.sleep(0.5)

        all_recs = tele.get_all_telemetry()
        assert len(all_recs) == 2
        ids = {r.module_id for r in all_recs}
        assert "mod.a" in ids
        assert "mod.b" in ids
        tele.shutdown()

    def test_metadata_persisted(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("mod.x", 5.0, "success", metadata={"claims_count": 12})
        time.sleep(0.5)

        rec = tele.get_telemetry("mod.x")
        assert rec is not None
        assert rec.metadata == {"claims_count": 12}
        tele.shutdown()


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    def test_record_and_get_via_module_functions(self, tmp_db, monkeypatch):
        import trustforge.module_telemetry as mt
        monkeypatch.setattr(mt, "_DEFAULT_DB_PATH", tmp_db)

        mt.record_invocation("test.module", 33.3, "success")
        time.sleep(0.5)
        rec = mt.get_telemetry("test.module")
        assert rec is not None
        assert rec.module_id == "test.module"

    def test_failure_does_not_raise(self, monkeypatch):
        """Telemetry failure must not bubble up."""
        import trustforge.module_telemetry as mt
        monkeypatch.setattr(mt, "_DEFAULT_DB_PATH", "/nonexistent/path/db.sqlite3")
        # Should not raise
        mt.record_invocation("broken", 1.0, "failure")
        # get_telemetry should return None gracefully
        assert mt.get_telemetry("broken") is None


class TestFailSafe:
    """Telemetry must never crash the caller."""

    def test_queue_full_does_not_raise(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        # Fill the queue (should just drop silently)
        for i in range(3000):
            tele.record_invocation(f"flood.{i}", 1.0, "success")
        # Should not raise
        tele.shutdown()
