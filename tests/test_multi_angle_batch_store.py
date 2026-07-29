from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from trustforge.multi_angle_batch_store import (
    AtomicBatchRequest,
    BatchConflictError,
    BatchStoreBackendError,
    BatchStoreIntegrityError,
    DynamoDBAtomicMultiAngleBatchStore,
    SQLiteAtomicMultiAngleBatchStore,
    _job_ids,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _request(batch_id: str, *, fingerprint: str | None = None) -> AtomicBatchRequest:
    return AtomicBatchRequest(
        batch_id=batch_id,
        caller_hash=_hash("caller-a"),
        idempotency_key_hash=_hash(f"key-{batch_id}"),
        request_fingerprint=fingerprint or _hash(f"payload-{batch_id}"),
        coin="BTC",
        snapshot_id=f"snap-{batch_id}",
        day="2026-07-29",
        batch_cost_usd=Decimal("0.50"),
        config_version="v1",
        created_at=1_785_283_200,
    )


def _sqlite_store(tmp_path, remaining="0.50"):
    store = SQLiteAtomicMultiAngleBatchStore(str(tmp_path / "batch.db"))
    store.bootstrap_budget(
        day="2026-07-29", remaining_usd=Decimal(remaining), config_version="v1"
    )
    return store


def test_sqlite_contract_creates_request_batch_five_allocations_and_jobs(tmp_path):
    store = _sqlite_store(tmp_path)
    result = store.create_batch(_request("a"))
    assert result.admitted and not result.replayed and len(result.job_ids) == 5
    with sqlite3.connect(tmp_path / "batch.db") as conn:
        assert conn.execute("SELECT count(*) FROM atomic_requests").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM atomic_batches").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM atomic_allocations").fetchone()[0] == 5
        assert conn.execute("SELECT count(*) FROM atomic_jobs").fetchone()[0] == 5
        assert conn.execute("SELECT remaining_usd FROM atomic_budget").fetchone()[0] == "0.00"


def test_missing_or_stale_authoritative_budget_fails_closed(tmp_path):
    store = SQLiteAtomicMultiAngleBatchStore(str(tmp_path / "batch.db"))
    with pytest.raises(BatchStoreBackendError):
        store.create_batch(_request("missing"))
    store.bootstrap_budget(
        day="2026-07-29", remaining_usd=Decimal(1), config_version="stale"
    )
    with pytest.raises(BatchStoreBackendError):
        store.create_batch(_request("stale"))


def test_same_caller_key_replays_and_different_fingerprint_conflicts(tmp_path):
    store = _sqlite_store(tmp_path)
    request = _request("same")
    assert not store.create_batch(request).replayed
    assert store.create_batch(request).replayed
    with pytest.raises(BatchConflictError):
        store.create_batch(
            AtomicBatchRequest(
                **{**request.__dict__, "request_fingerprint": _hash("different")}
            )
        )


def test_sqlite_same_key_retry_returns_original_batch_id(tmp_path):
    store = _sqlite_store(tmp_path)
    original = _request("original")
    store.create_batch(original)
    retry = AtomicBatchRequest(
        **{
            **original.__dict__,
            "batch_id": "newly-generated-batch",
            "snapshot_id": "newly-generated-snapshot",
        }
    )
    result = store.create_batch(retry)
    assert result.replayed
    assert result.batch_id == "original"
    assert result.job_ids == _job_ids("original")


def test_stale_caller_cannot_replay_another_callers_key(tmp_path):
    store = _sqlite_store(tmp_path, "1")
    original = _request("bound")
    store.create_batch(original)
    impostor = AtomicBatchRequest(
        **{
            **original.__dict__,
            "caller_hash": _hash("caller-b"),
            "batch_id": "bound-b",
            "snapshot_id": "snap-bound-b",
        }
    )
    result = store.create_batch(impostor)
    assert result.admitted and not result.replayed


def test_sqlite_concurrent_batches_compete_for_last_capacity(tmp_path):
    db = str(tmp_path / "batch.db")
    stores = [SQLiteAtomicMultiAngleBatchStore(db), SQLiteAtomicMultiAngleBatchStore(db)]
    stores[0].bootstrap_budget(
        day="2026-07-29", remaining_usd=Decimal("0.50"), config_version="v1"
    )
    barrier = threading.Barrier(2)

    def submit(index):
        barrier.wait()
        return stores[index].create_batch(_request(f"batch-{index}"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(submit, 0), pool.submit(submit, 1)]]
    assert sum(result.admitted for result in results) == 1


def test_sqlite_fault_rolls_back_budget_request_and_manifest(tmp_path):
    store = _sqlite_store(tmp_path)
    with sqlite3.connect(tmp_path / "batch.db") as conn:
        conn.execute(
            "INSERT INTO atomic_jobs VALUES(?,?,?,?)",
            (_job_ids("fault")[2], "other", "news", "pending"),
        )
    with pytest.raises(BatchStoreBackendError):
        store.create_batch(_request("fault"))
    with sqlite3.connect(tmp_path / "batch.db") as conn:
        assert conn.execute("SELECT remaining_usd FROM atomic_budget").fetchone()[0] == "0.50"
        assert conn.execute("SELECT count(*) FROM atomic_requests").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM atomic_batches").fetchone()[0] == 0


def test_replay_with_incomplete_manifest_is_integrity_error(tmp_path):
    store = _sqlite_store(tmp_path)
    request = _request("incomplete")
    store.create_batch(request)
    with sqlite3.connect(tmp_path / "batch.db") as conn:
        conn.execute("DELETE FROM atomic_jobs WHERE job_id=?", (_job_ids("incomplete")[0],))
    with pytest.raises(BatchStoreIntegrityError):
        store.create_batch(request)


class _Client:
    def __init__(
        self,
        *,
        cancel_index=None,
        replay=False,
        incomplete=False,
        budget_remaining="0.00",
        budget_version="v1",
        budget_missing=False,
    ):
        self.cancel_index = cancel_index
        self.replay = replay
        self.incomplete = incomplete
        self.budget_remaining = budget_remaining
        self.budget_version = budget_version
        self.budget_missing = budget_missing
        self.calls = []
        self.request = _request("ddb")

    def get_item(self, **kwargs):
        if kwargs["Key"]["pk"]["S"].startswith("BUDGET#"):
            if self.budget_missing:
                return {}
            return {
                "Item": {
                    **kwargs["Key"],
                    "remaining_usd": {"N": self.budget_remaining},
                    "config_version": {"S": self.budget_version},
                }
            }
        if not self.replay:
            return {}
        return {
            "Item": {
                **kwargs["Key"],
                "request_fingerprint": {"S": self.request.request_fingerprint},
                "batch_id": {"S": self.request.batch_id},
            }
        }

    def batch_get_item(self, **kwargs):
        keys = kwargs["RequestItems"]["sandbox"]["Keys"]
        if self.incomplete:
            keys = keys[:-1]
        items = []
        for key in keys:
            item = {**key}
            pk = key["pk"]["S"]
            sk = key["sk"]["S"]
            if pk.startswith("BATCH#") and sk == "META":
                item.update(
                    {
                        "request_fingerprint": {
                            "S": self.request.request_fingerprint
                        },
                        "caller_hash": {"S": self.request.caller_hash},
                    }
                )
            elif pk.startswith("BATCH#") and sk.startswith("ALLOCATION#"):
                mode = sk.removeprefix("ALLOCATION#")
                item["job_id"] = {"S": _job_ids(self.request.batch_id)[
                    ("risk", "sentiment", "fundamentals", "news", "catalyst").index(mode)
                ]}
            elif pk.startswith("JOB#"):
                job_id = pk.removeprefix("JOB#")
                mode = ("risk", "sentiment", "fundamentals", "news", "catalyst")[
                    _job_ids(self.request.batch_id).index(job_id)
                ]
                item.update(
                    {
                        "batch_id": {"S": self.request.batch_id},
                        "mode": {"S": mode},
                    }
                )
            items.append(item)
        return {
            "Responses": {
                "sandbox": items,
            }
        }

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        if self.cancel_index is None:
            return {}
        reasons = [{"Code": "None"} for _ in kwargs["TransactItems"]]
        indexes = (
            self.cancel_index if isinstance(self.cancel_index, tuple) else (self.cancel_index,)
        )
        for index in indexes:
            reasons[index] = {"Code": "ConditionalCheckFailed"}
        if 1 in indexes:
            self.replay = True
        raise ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                "CancellationReasons": reasons,
            },
            "TransactWriteItems",
        )


def test_dynamodb_contract_is_one_thirteen_action_transaction_without_ttl():
    client = _Client()
    result = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).create_batch(client.request)
    assert result.admitted
    writes = client.calls[0]["TransactItems"]
    assert len(writes) == 13
    assert "attribute_exists(remaining_usd)" in writes[0]["Update"]["ConditionExpression"]
    assert "remaining_usd = remaining_usd - :cost" in writes[0]["Update"]["UpdateExpression"]
    assert all(
        "expires_at" not in operation.get("Put", {}).get("Item", {})
        for operation in writes
    )


def test_dynamodb_budget_cancellation_is_denied():
    client = _Client(cancel_index=0)
    result = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).create_batch(client.request)
    assert not result.admitted


@pytest.mark.parametrize(
    "client",
    [
        _Client(cancel_index=0, budget_missing=True),
        _Client(cancel_index=0, budget_version="stale"),
    ],
)
def test_dynamodb_missing_or_stale_budget_cancellation_fails_closed(client):
    with pytest.raises(BatchStoreBackendError):
        DynamoDBAtomicMultiAngleBatchStore(
            client=client, table_name="sandbox"
        ).create_batch(client.request)


def test_dynamodb_unexplained_budget_cancellation_is_integrity_error():
    client = _Client(cancel_index=0, budget_remaining="1.00")
    with pytest.raises(BatchStoreIntegrityError):
        DynamoDBAtomicMultiAngleBatchStore(
            client=client, table_name="sandbox"
        ).create_batch(client.request)


def test_dynamodb_request_collision_rereads_complete_manifest():
    client = _Client(cancel_index=1)
    result = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).create_batch(client.request)
    assert result.admitted and result.replayed


def test_dynamodb_same_key_retry_returns_original_batch_id():
    client = _Client(replay=True)
    retry = AtomicBatchRequest(
        **{
            **client.request.__dict__,
            "batch_id": "newly-generated-batch",
            "snapshot_id": "newly-generated-snapshot",
        }
    )
    result = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).create_batch(retry)
    assert result.replayed
    assert result.batch_id == client.request.batch_id
    assert result.job_ids == _job_ids(client.request.batch_id)


def test_dynamodb_racing_replay_wins_over_simultaneous_budget_denial():
    client = _Client(cancel_index=(0, 1))
    result = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).create_batch(client.request)
    assert result.admitted and result.replayed


def test_dynamodb_other_cancellation_fails_closed():
    client = _Client(cancel_index=2)
    with pytest.raises(BatchStoreBackendError):
        DynamoDBAtomicMultiAngleBatchStore(
            client=client, table_name="sandbox"
        ).create_batch(client.request)


def test_dynamodb_incomplete_replay_manifest_is_integrity_error():
    client = _Client(replay=True, incomplete=True)
    with pytest.raises(BatchStoreIntegrityError):
        DynamoDBAtomicMultiAngleBatchStore(
            client=client, table_name="sandbox"
        ).create_batch(client.request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("day", "2026-7-1"),
        ("caller_hash", "raw-ip"),
        ("idempotency_key_hash", "short"),
        ("batch_cost_usd", Decimal("NaN")),
        ("batch_cost_usd", Decimal("0.0000001")),
        ("batch_id", "../bad"),
        ("day", "2026-07-28"),
    ],
)
def test_input_validation_rejects_unbounded_or_noncanonical(field, value):
    request = _request("valid")
    invalid = AtomicBatchRequest(**{**request.__dict__, field: value})
    with pytest.raises(ValueError):
        invalid.validate()
