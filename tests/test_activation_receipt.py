"""Unit tests for activation_receipt.py"""
from __future__ import annotations

import json
import tempfile

from trustforge.activation_receipt import (
    ActivationReceipt,
    read_receipts_local,
    write_receipt_local,
)


def test_receipt_to_json_line():
    r = ActivationReceipt(
        activation_target="i-123",
        owner_id="owner-a",
        candidate_digest="abc123",
        previous_active_digest="prev",
        status="completed",
        build_timestamp="2026-01-01T00:00:00Z",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:01:00Z",
        error="",
        rollback_triggered=False,
        rollback_succeeded=False,
    )
    line = r.to_json_line()
    assert line.endswith("\n")
    d = json.loads(line)
    assert d["activation_target"] == "i-123"
    assert d["status"] == "completed"
    assert d["receipt_version"] == "trustforge.activation-receipt/v1"
    assert d["rollback_triggered"] is False
    assert d["rollback_succeeded"] is False


def test_receipt_roundtrip_local():
    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/receipts.jsonl"
        r = ActivationReceipt(
            activation_target="i-123",
            owner_id="owner-a",
            candidate_digest="abc123",
            previous_active_digest="prev",
            status="completed",
            build_timestamp="2026-01-01T00:00:00Z",
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:01:00Z",
            error="",
            rollback_triggered=False,
            rollback_succeeded=False,
        )
        assert write_receipt_local(r, path=path)
        receipts = read_receipts_local(path=path)
        assert len(receipts) == 1
        assert receipts[0].activation_target == "i-123"
        assert receipts[0].status == "completed"


def test_receipt_append_only():
    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/receipts.jsonl"
        r1 = ActivationReceipt(
            activation_target="i-123",
            owner_id="owner-a",
            candidate_digest="abc123",
            previous_active_digest="",
            status="completed",
            build_timestamp="2026-01-01T00:00:00Z",
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:30Z",
            error="",
            rollback_triggered=False,
            rollback_succeeded=False,
        )
        r2 = ActivationReceipt(
            activation_target="i-123",
            owner_id="owner-a",
            candidate_digest="abc124",
            previous_active_digest="abc123",
            status="rolled_back",
            build_timestamp="2026-01-01T00:00:00Z",
            started_at="2026-01-01T00:01:00Z",
            finished_at="2026-01-01T00:01:30Z",
            error="healthz failed",
            rollback_triggered=True,
            rollback_succeeded=True,
        )
        assert write_receipt_local(r1, path=path)
        assert write_receipt_local(r2, path=path)
        receipts = read_receipts_local(path=path)
        assert len(receipts) == 2
        assert receipts[0].candidate_digest == "abc123"
        assert receipts[1].candidate_digest == "abc124"
        assert receipts[1].rollback_triggered is True
        assert receipts[1].rollback_succeeded is True


def test_receipt_from_json_line():
    r = ActivationReceipt(
        activation_target="i-456",
        owner_id="owner-b",
        candidate_digest="def456",
        previous_active_digest="abc123",
        status="rollback_failed",
        build_timestamp="2026-02-01T00:00:00Z",
        started_at="2026-02-01T00:00:00Z",
        finished_at="2026-02-01T00:00:05Z",
        error="rollback incomplete",
        rollback_triggered=True,
        rollback_succeeded=False,
    )
    line = r.to_json_line()
    parsed = ActivationReceipt.from_json_line(line)
    assert parsed.activation_target == "i-456"
    assert parsed.status == "rollback_failed"
    assert parsed.error == "rollback incomplete"
    assert parsed.rollback_triggered is True
    assert parsed.rollback_succeeded is False


def test_read_empty_local():
    with tempfile.TemporaryDirectory() as td:
        receipts = read_receipts_local(path=f"{td}/nonexistent.jsonl")
        assert receipts == []


def test_receipt_bool_coercion():
    """Boolean fields should be coerced to bool in JSON output."""
    r = ActivationReceipt(
        activation_target="i-1",
        owner_id="o",
        candidate_digest="c",
        previous_active_digest="p",
        status="completed",
        build_timestamp="2026-01-01T00:00:00Z",
        started_at="2026-01-01T00:00:00Z",
        rollback_triggered=1,
        rollback_succeeded=0,
    )
    line = r.to_json_line()
    d = json.loads(line)
    assert d["rollback_triggered"] is True
    assert d["rollback_succeeded"] is False
