"""Tests for module_telemetry.py (issue #382)."""
from __future__ import annotations

import json
import os
import tempfile
import threading

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
        assert tele.flush()

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
        assert tele.flush()

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
        assert tele.flush()

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
        assert tele.flush()

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
        assert mt.flush()
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

    def test_flush_reports_writer_failure_instead_of_fake_success(
        self, tmp_db, monkeypatch
    ):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)

        def _fail_write(events):
            raise OSError("injected durable write failure")

        monkeypatch.setattr(tele._store, "write_batch", _fail_write)
        tele.record_invocation("broken.flush", 1.0)
        assert tele.flush(timeout=1.0) is False
        tele.shutdown()

    def test_flush_times_out_when_full_queue_is_blocked(
        self, tmp_db, monkeypatch
    ):
        from trustforge.module_telemetry import ModuleTelemetry, _QUEUE_MAX

        tele = ModuleTelemetry(db_path=tmp_db)
        write_entered = threading.Event()
        release_write = threading.Event()
        real_write_batch = tele._store.write_batch

        def _blocked_write(events):
            write_entered.set()
            assert release_write.wait(timeout=2.0)
            real_write_batch(events)

        monkeypatch.setattr(tele._store, "write_batch", _blocked_write)
        tele.record_invocation("blocked.first", 1.0)
        assert write_entered.wait(timeout=1.0)
        for index in range(_QUEUE_MAX):
            tele.record_invocation(f"blocked.{index}", 1.0)
        assert tele.flush(timeout=0.01) is False
        release_write.set()
        tele.shutdown()

    def test_shutdown_reaps_writer_after_barrier(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("shutdown.barrier", 1.0)
        assert tele.flush(timeout=1.0)
        tele.shutdown()
        assert tele._writer_thread is not None
        assert not tele._writer_thread.is_alive()


class TestModuleStateV2:
    """Issue #382: 狀態模型 registered → configured → resolved → invoked → verified。"""

    def test_invoked_state(self, tmp_db):
        """record_invocation() 後 state 應為 'invoked'。"""
        from trustforge.module_telemetry import ModuleTelemetry, ModuleState

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("trust.scoring", 50.0, "success")
        assert tele.flush()

        rec = tele.get_telemetry("trust.scoring")
        assert rec is not None
        assert rec.state == ModuleState.invoked.value
        assert rec.state == "invoked"
        tele.shutdown()

    def test_verified_state(self, tmp_db):
        """record_verified() 後 state 應為 'verified'。"""
        from trustforge.module_telemetry import ModuleTelemetry, ModuleState

        tele = ModuleTelemetry(db_path=tmp_db)
        # 先 invoke
        tele.record_invocation("trust.scoring", 50.0, "success")
        assert tele.flush()
        # 再 verify
        tele.record_verified("trust.scoring", "tests/test_trust_scoring.py::test_basic")
        assert tele.flush()

        rec = tele.get_telemetry("trust.scoring")
        assert rec is not None
        assert rec.state == ModuleState.verified.value
        assert rec.state == "verified"
        assert rec.evidence_ref == "tests/test_trust_scoring.py::test_basic"
        tele.shutdown()

    def test_unregistered_module_returns_none(self, tmp_db):
        """未呼叫的模組查詢回傳 None（等同 registered 語義：存在但未追蹤）。"""
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        # 從未記錄任何事件
        rec = tele.get_telemetry("never.called.module")
        assert rec is None
        tele.shutdown()

    def test_default_state_is_registered(self):
        """TelemetryRecord 的 state 預設值為 'registered'。"""
        from trustforge.module_telemetry import TelemetryRecord, ModuleState

        rec = TelemetryRecord(
            module_id="test.mod",
            last_invoked_at="2026-07-22T00:00:00Z",
            invocation_count=0,
            last_result="unknown",
            avg_latency_ms=0.0,
        )
        assert rec.state == ModuleState.registered.value
        assert rec.state == "registered"

    def test_module_state_enum_values(self):
        """確認 ModuleState 所有期望的狀態值都存在。"""
        from trustforge.module_telemetry import ModuleState

        expected = {
            "registered", "configured", "resolved", "invoked", "verified",
            "disabled", "blocked", "degraded", "failed", "stale",
        }
        actual = {s.value for s in ModuleState}
        assert actual == expected

    def test_verified_preserves_evidence_ref_on_subsequent_invoke(self, tmp_db):
        """verified 後再次 invoke 不應清掉 evidence_ref（CASE 語句）。"""
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("mod.a", 10.0, "success")
        assert tele.flush()
        tele.record_verified("mod.a", "ci/run-123")
        assert tele.flush()
        # 再次 invoke（evidence_ref 為空字串，不應覆蓋）
        tele.record_invocation("mod.a", 20.0, "success")
        assert tele.flush()

        rec = tele.get_telemetry("mod.a")
        assert rec is not None
        # state 會被覆蓋為 invoked（最後事件）
        assert rec.state == "invoked"
        # evidence_ref 保留（CASE WHEN '' 不覆蓋）
        assert rec.evidence_ref == "ci/run-123"
        tele.shutdown()

    def test_module_level_record_verified(self, tmp_db, monkeypatch):
        """Module-level record_verified() 便捷函式。"""
        import trustforge.module_telemetry as mt
        monkeypatch.setattr(mt, "_DEFAULT_DB_PATH", tmp_db)

        mt.record_invocation("agent.build_report", 100.0, "success")
        assert mt.flush()
        mt.record_verified("agent.build_report", "tests/test_orchestrator.py::test_build_report")
        assert mt.flush()

        rec = mt.get_telemetry("agent.build_report")
        assert rec is not None
        assert rec.state == "verified"
        assert rec.evidence_ref == "tests/test_orchestrator.py::test_build_report"

    def test_configured_resolved_invoked_verified_lifecycle(self, tmp_db):
        """Issue #382: configured/resolved states are first-class evidence."""
        from trustforge.module_telemetry import ModuleState, ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_state(
            "provider.llm",
            ModuleState.configured,
            evidence_ref="env:BEDROCK_MODEL_ID",
            metadata={"phase": "configured"},
        )
        assert tele.flush()
        rec = tele.get_telemetry("provider.llm")
        assert rec is not None
        assert rec.state == "configured"
        assert rec.last_result == "configured"
        assert rec.invocation_count == 0
        assert rec.avg_latency_ms == 0.0
        assert rec.last_latency_ms == 0.0
        assert rec.evidence_ref == "env:BEDROCK_MODEL_ID"
        assert rec.metadata == {"phase": "configured"}

        tele.record_state(
            "provider.llm",
            "resolved",
            evidence_ref="provider.resolve:llm",
            metadata={"resolved": "bedrock"},
        )
        assert tele.flush()
        rec = tele.get_telemetry("provider.llm")
        assert rec is not None
        assert rec.state == "resolved"
        assert rec.last_result == "resolved"
        assert rec.invocation_count == 0
        assert rec.avg_latency_ms == 0.0
        assert rec.last_latency_ms == 0.0
        assert rec.evidence_ref == "provider.resolve:llm"
        assert rec.metadata == {"resolved": "bedrock"}

        tele.record_invocation("provider.llm", 12.5, "success", metadata={"tool": "complete"})
        assert tele.flush()
        rec = tele.get_telemetry("provider.llm")
        assert rec is not None
        assert rec.state == "invoked"
        assert rec.invocation_count == 1
        assert rec.avg_latency_ms == pytest.approx(12.5, rel=0.01)
        assert rec.last_latency_ms == pytest.approx(12.5, rel=0.01)
        assert rec.evidence_ref == "provider.resolve:llm"

        tele.record_verified("provider.llm", "ci:test_provider_ports")
        assert tele.flush()
        rec = tele.get_telemetry("provider.llm")
        assert rec is not None
        assert rec.state == "verified"
        assert rec.last_result == "verified"
        assert rec.invocation_count == 1
        assert rec.avg_latency_ms == pytest.approx(12.5, rel=0.01)
        assert rec.last_latency_ms == pytest.approx(12.5, rel=0.01)
        assert rec.evidence_ref == "ci:test_provider_ports"
        tele.shutdown()


class TestPublicApiWhitelist:
    """#636: /api/module-telemetry 是無認證端點，不得原樣外流
    `evidence_ref`（可能含測試名/CI URL/程式碼位置）與任意 `metadata`。
    """

    _ALLOWED_KEYS = {
        "module_id",
        "state",
        "last_invoked_at",
        "invocation_count",
        "avg_latency_ms",
        "last_latency_ms",
        "last_result",
    }

    def test_to_public_dict_excludes_evidence_ref_and_metadata(self, tmp_db):
        from trustforge.module_telemetry import ModuleTelemetry

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation(
            "agent.build_report", 42.0, "success",
            metadata={"internal_path": "/etc/secrets/keys.json"},
        )
        assert tele.flush()

        rec = tele.get_telemetry("agent.build_report")
        assert rec is not None
        assert rec.metadata  # sanity: internal field is populated pre-serialization

        tele.record_verified(
            "agent.build_report",
            "https://ci.internal.example.com/jobs/12345",
        )
        assert tele.flush()

        rec = tele.get_telemetry("agent.build_report")
        assert rec is not None
        assert rec.evidence_ref  # sanity: internal field still populated

        public = rec.to_public_dict()
        assert set(public.keys()) == self._ALLOWED_KEYS
        assert "evidence_ref" not in public
        assert "metadata" not in public
        # 佐證 evidence_ref/metadata 的實際內容確實沒有經任何管道混進白名單值
        for value in public.values():
            assert "ci.internal.example.com" not in str(value)
            assert "/etc/secrets" not in str(value)

        tele.shutdown()

    def test_api_module_telemetry_endpoint_single_module_hides_sensitive_fields(
        self, tmp_db, monkeypatch
    ):
        from trustforge import web
        from trustforge.module_telemetry import ModuleTelemetry
        import trustforge.module_telemetry as mt

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation("agent.build_report", 10.0, "success")
        assert tele.flush()
        tele.record_verified(
            "agent.build_report",
            "tests/test_orchestrator.py::test_build_report",
        )
        assert tele.flush()

        monkeypatch.setattr(mt, "get_telemetry", tele.get_telemetry)
        monkeypatch.setattr(mt, "get_all_telemetry", tele.get_all_telemetry)

        code, body = web._handle_api_module_telemetry({"module_id": ["agent.build_report"]})
        assert code == 200
        data = json.loads(body)["data"]
        assert set(data.keys()) == self._ALLOWED_KEYS
        assert "evidence_ref" not in data
        assert "metadata" not in data
        assert data["module_id"] == "agent.build_report"
        assert data["state"] == "verified"

        tele.shutdown()

    def test_api_module_telemetry_endpoint_list_hides_sensitive_fields(
        self, tmp_db, monkeypatch
    ):
        from trustforge import web
        from trustforge.module_telemetry import ModuleTelemetry
        import trustforge.module_telemetry as mt

        tele = ModuleTelemetry(db_path=tmp_db)
        tele.record_invocation(
            "agent.build_report", 10.0, "success",
            metadata={"secret": "should-not-leak"},
        )
        assert tele.flush()
        tele.record_verified("agent.build_report", "ci://internal-run-999")
        assert tele.flush()

        monkeypatch.setattr(mt, "get_telemetry", tele.get_telemetry)
        monkeypatch.setattr(mt, "get_all_telemetry", tele.get_all_telemetry)

        code, body = web._handle_api_module_telemetry(None)
        assert code == 200
        data = json.loads(body)["data"]
        assert data["total"] >= 1
        for module in data["modules"]:
            assert set(module.keys()) == self._ALLOWED_KEYS
            assert "evidence_ref" not in module
            assert "metadata" not in module

        tele.shutdown()
