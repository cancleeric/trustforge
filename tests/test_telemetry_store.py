"""Telemetry store protocol and adapter tests (#411)."""
from __future__ import annotations

import time

import pytest

from trustforge.module_telemetry import ModuleTelemetry
from trustforge.telemetry_store import (
    SQLiteTelemetryStore,
    TelemetryStore,
    TelemetryStoreEvent,
    TelemetryStoreRecord,
)


class FailingTelemetryStore:
    """Store fake that fails every operation after initialize."""

    def __init__(self) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    def write_batch(self, batch: list[TelemetryStoreEvent]) -> None:
        raise RuntimeError("storage unavailable")

    def get(self, subject_id: str) -> TelemetryStoreRecord | None:
        raise RuntimeError("storage unavailable")

    def list_all(self) -> list[TelemetryStoreRecord]:
        raise RuntimeError("storage unavailable")


def test_sqlite_store_satisfies_protocol(tmp_path):
    store = SQLiteTelemetryStore(str(tmp_path / "telemetry.sqlite3"))

    assert isinstance(store, TelemetryStore)


def test_sqlite_store_records_lifecycle_transition(tmp_path):
    store = SQLiteTelemetryStore(str(tmp_path / "telemetry.sqlite3"))
    store.initialize()

    store.write_batch([
        TelemetryStoreEvent(
            subject_id="platform.consumer",
            latency_ms=12.5,
            result="success",
            ts=1_800_000_000.0,
            state="invoked",
            metadata={"consumer": "unit"},
        ),
        TelemetryStoreEvent(
            subject_id="platform.consumer",
            latency_ms=0.0,
            result="verified",
            ts=1_800_000_001.0,
            state="verified",
            evidence_ref="tests/test_telemetry_store.py",
        ),
    ])

    rec = store.get("platform.consumer")

    assert rec == TelemetryStoreRecord(
        subject_id="platform.consumer",
        last_invoked_at="2027-01-15T08:00:01Z",
        invocation_count=2,
        last_result="verified",
        avg_latency_ms=6.25,
        last_latency_ms=0.0,
        state="verified",
        evidence_ref="tests/test_telemetry_store.py",
        metadata={},
    )
    assert store.list_all() == [rec]


def test_module_telemetry_storage_failure_is_fail_closed_to_caller():
    store = FailingTelemetryStore()
    tele = ModuleTelemetry(db_path="/tmp/unused-module-telemetry.sqlite3", store=store)

    tele.record_invocation("mod.failure", 1.0, "failure")
    time.sleep(0.2)

    assert store.initialized is True
    assert tele.get_telemetry("mod.failure") is None
    assert tele.get_all_telemetry() == []
    tele.shutdown()


def test_module_telemetry_shutdown_flushes_pending_events(tmp_path):
    db_path = str(tmp_path / "telemetry.sqlite3")
    tele = ModuleTelemetry(db_path=db_path)

    tele.record_invocation("mod.shutdown", 15.0, "success")
    tele.shutdown()

    rec = SQLiteTelemetryStore(db_path).get("mod.shutdown")
    assert rec is not None
    assert rec.invocation_count == 1
    assert rec.last_result == "success"


def test_module_telemetry_queue_full_drops_without_raise(tmp_path, monkeypatch):
    tele = ModuleTelemetry(db_path=str(tmp_path / "telemetry.sqlite3"))
    monkeypatch.setattr(tele._queue, "put_nowait", lambda event: (_ for _ in ()).throw(__import__("queue").Full()))

    tele.record_invocation("mod.full", 1.0, "success")
    tele.record_verified("mod.full", "unit")

    tele.shutdown()


def test_store_contract_has_no_trustforge_domain_imports():
    forbidden = {"coin", "claim", "evidence", "stance", "hermes", "trustforge"}
    fields = {
        *TelemetryStoreEvent.__dataclass_fields__,
        *TelemetryStoreRecord.__dataclass_fields__,
    }

    assert fields.isdisjoint(forbidden)
