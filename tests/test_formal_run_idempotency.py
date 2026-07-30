from __future__ import annotations

import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from trustforge.formal_run_idempotency import (
    BadIdempotencyKey,
    FormalRunIdentity,
    FormalRunLookup,
    FormalRunReceipt,
    HmacValue,
    IdempotencyUnavailable,
    ParsedIdempotencyKey,
    IdempotencyInProgress,
    StaleFencingToken,
    TerminalSafeResponse,
    accepted_acquisition_epochs,
    build_identity,
    canonical_request_tuple,
    parse_idempotency_key,
    request_fingerprint,
)
from trustforge.formal_run_idempotency_sqlite import SqliteFormalRunIdempotencyStore

NOW = datetime(2026, 7, 30, 8, tzinfo=timezone.utc)


def key(epoch: str = "202607", byte: int = 7) -> str:
    random_part = base64.urlsafe_b64encode(bytes([byte]) * 16).decode().rstrip("=")
    return f"tf1.{epoch}.{random_part}"


def identity(raw_key: str | None = None, caller: str = "tenant-a"):
    parsed = parse_idempotency_key(raw_key or key())
    return parsed, build_identity(
        namespace="formal-analysis",
        caller_scope=caller,
        parsed_key=parsed,
        caller_secret=b"c" * 32,
        caller_key_id="caller-v1",
        idempotency_secret=b"k" * 32,
        idempotency_key_id="key-v1",
        retention_locator_secret=b"l" * 32,
    )


def fingerprint(*, question: str = "Assess risk", locale: str = "zh-Hant", fresh: bool = False):
    return request_fingerprint(
        b"f" * 32,
        "fingerprint-v1",
        coin=" btc ",
        mode="risk",
        question=question,
        locale=locale,
        fresh=fresh,
    )


def lookup(parsed, ident, fp=None, *, candidate_identities=(), candidate_fingerprints=()):
    return FormalRunLookup(
        parsed_key=parsed,
        primary_identity=ident,
        primary_fingerprint=fp or fingerprint(),
        candidate_identities=tuple(candidate_identities),
        candidate_fingerprints=tuple(candidate_fingerprints),
    )


def versioned_identity(
    parsed, *, caller_key_id: str, caller_secret: bytes,
    key_id: str, key_secret: bytes,
):
    return build_identity(
        namespace="formal-analysis", caller_scope="tenant-a", parsed_key=parsed,
        caller_secret=caller_secret, caller_key_id=caller_key_id,
        idempotency_secret=key_secret, idempotency_key_id=key_id,
        retention_locator_secret=b"l" * 32,
    )


def bind_chargeable(store, ident, token, *, suffix: str = ""):
    bound_receipt = FormalRunReceipt(
        receipt_id=f"frc{suffix or '_base'}", question_id=f"q{suffix or '_base'}",
        job_id=f"job{suffix or '_base'}", result_id=f"result{suffix or '_base'}",
        state="accepted", origin="manual", disposition="created", locale="zh-Hant",
        created_at="2026-07-30T08:00:00Z",
    )
    store.bind(
        identity=ident, fencing_token=token, receipt=bound_receipt,
        operation_id=f"op{suffix or '_base'}", outbox_state="pending",
        dispatch_state="not_dispatched", reservation_id=f"res{suffix or '_base'}",
        max_reserved_cost="1", provider_operation_id=f"provider{suffix or '_base'}",
        cost_policy_version="cost-v1", cost_policy_digest="d" * 64,
        settlement_state="reserved", reconciliation_state="pending", now=NOW,
    )
    return bound_receipt


def receipt(*, state: str = "accepted", disposition: str = "created") -> FormalRunReceipt:
    return FormalRunReceipt(
        receipt_id="frc_1",
        question_id="q_1",
        job_id="job_1",
        result_id="result_1",
        state=state,  # type: ignore[arg-type]
        origin="manual",
        disposition=disposition,  # type: ignore[arg-type]
        locale="zh-Hant",
        created_at="2026-07-30T08:00:00Z",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " " + key(),
        key() + " ",
        key() + ",other",
        key() + "\n",
        "tf1.202607.short",
        "tf1.202613." + key().rsplit(".", 1)[1],
        ["one", "two"],
    ],
)
def test_key_parser_rejects_ambiguous_or_noncanonical_values(raw):
    with pytest.raises(BadIdempotencyKey):
        parse_idempotency_key(raw)


def test_key_parser_accepts_exact_128_bit_base64url_key():
    parsed = parse_idempotency_key([key()])
    assert parsed.epoch == "202607"
    assert parsed.raw == key()


def test_fingerprint_is_length_prefixed_normalized_and_fresh_sensitive():
    omitted = fingerprint()
    explicit_false = fingerprint(fresh=False)
    assert omitted == explicit_false
    assert fingerprint(fresh=True) != omitted
    assert fingerprint(locale="en") != omitted
    assert fingerprint(question="Assess  risk") != fingerprint(question="Assess risk")
    assert canonical_request_tuple(
        coin=" btc ", mode=" risk ", question="  Assess Risk  ", locale="zh-TW"
    ) == canonical_request_tuple(
        coin="BTC", mode="risk", question="Assess Risk", locale="zh-Hant"
    )


def test_hmac_purposes_and_callers_are_isolated():
    parsed, first = identity(caller="tenant-a")
    _, second = identity(caller="tenant-b")
    assert first.caller_scope_hmac.digest != second.caller_scope_hmac.digest
    assert first.key_hmac.digest == second.key_hmac.digest
    assert first.key_hmac.digest != first.caller_scope_hmac.digest
    with pytest.raises(IdempotencyUnavailable):
        build_identity(
            namespace="formal-analysis",
            caller_scope="",
            parsed_key=parsed,
            caller_secret=b"a" * 32,
            caller_key_id="a",
            idempotency_secret=b"b" * 32,
            idempotency_key_id="b",
            retention_locator_secret=b"l" * 32,
        )


def test_epoch_horizon_requires_trusted_utc_clock():
    assert accepted_acquisition_epochs(NOW) == {"202607", "202606"}
    assert accepted_acquisition_epochs(datetime(2026, 1, 1, tzinfo=timezone.utc)) == {"202601", "202512"}
    with pytest.raises(IdempotencyUnavailable):
        accepted_acquisition_epochs(datetime(2026, 7, 30))


def test_sqlite_is_forbidden_in_production(tmp_path):
    for environment in ("production", "prod", "staging", "TEST", "", " development "):
        with pytest.raises(IdempotencyUnavailable):
            SqliteFormalRunIdempotencyStore(tmp_path / f"{environment!r}.db", environment=environment)
    SqliteFormalRunIdempotencyStore(tmp_path / "test.db", environment="test")
    SqliteFormalRunIdempotencyStore(tmp_path / "development.db", environment="development")


def test_sqlite_in_memory_store_persists_schema_across_connections():
    store = SqliteFormalRunIdempotencyStore(":memory:", environment="test")
    parsed, ident = identity()
    result = store.acquire(
        lookup=lookup(parsed, ident), now=NOW, lease_seconds=30
    )
    assert result.kind == "owner"


def test_runtime_dataclasses_reject_invalid_values():
    with pytest.raises(ValueError):
        HmacValue("key", "not-a-digest")
    valid_hmac = HmacValue("key-v1", "a" * 64)
    with pytest.raises(ValueError):
        FormalRunIdentity("bad namespace", "b" * 64, valid_hmac, valid_hmac)
    with pytest.raises(ValueError):
        FormalRunReceipt(
            receipt_id="bad id", question_id="q_1", job_id="job_1", result_id=None,
            state="accepted", origin="manual", disposition="created", locale="zh-Hant",
            created_at="2026-07-30T08:00:00Z",
        )
    with pytest.raises(ValueError):
        request_fingerprint(
            b"short", "fp-v1", coin="BTC", mode="risk", question="q",
            locale="zh-Hant",
        )


@pytest.mark.parametrize("value", ("0", "0001", "3601", "-1", "1.5", "99999"))
def test_retry_after_requires_canonical_bounded_delta_seconds(value):
    with pytest.raises(ValueError, match="delta-seconds"):
        TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1", body={},
            replay_headers={"Retry-After": value},
        )


def test_acquire_conflict_bind_and_immutable_replay(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity()
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    assert owner.kind == "owner"
    assert owner.fencing_token == 1
    assert store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    ).kind == "in_progress"
    assert store.acquire(
        lookup=lookup(parsed, ident, fingerprint(question="Different")),
        now=NOW,
        lease_seconds=30,
    ).kind == "conflict"

    store.bind(
        identity=ident,
        fencing_token=1,
        receipt=receipt(),
        operation_id="op_1",
        outbox_state="pending",
        dispatch_state="not_dispatched",
        reservation_id="res_1",
        max_reserved_cost="1.25",
        provider_operation_id="provider-op-1",
        cost_policy_version="cost-v1",
        cost_policy_digest="d" * 64,
        settlement_state="reserved",
        reconciliation_state="pending",
        now=NOW,
    )
    replay = store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW + timedelta(days=40), lease_seconds=30,
    )
    assert replay.kind == "replay"
    assert replay.receipt == receipt()


def test_expired_lease_takeover_fences_stale_owner(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="development")
    parsed, ident = identity()
    fp = fingerprint()
    first = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=1
    )
    second = store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW + timedelta(seconds=2), lease_seconds=30,
    )
    assert (first.fencing_token, second.fencing_token) == (1, 2)
    with pytest.raises(StaleFencingToken):
        store.bind(
                identity=ident, fencing_token=1, receipt=receipt(), operation_id="op_1",
                outbox_state="pending", dispatch_state="not_dispatched",
                reservation_id="res", max_reserved_cost="1", now=NOW + timedelta(seconds=2),
                provider_operation_id="provider-op", cost_policy_version="cost-v1",
                cost_policy_digest="d" * 64, settlement_state="reserved",
                reconciliation_state="pending",
        )


def test_closed_epoch_unknown_is_unavailable_but_retained_record_replays(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    old_parsed, old_identity = identity(key("202605"))
    assert store.acquire(
        lookup=lookup(old_parsed, old_identity),
        now=NOW, lease_seconds=30,
    ).kind == "key_unavailable"

    current, retained = identity(key("202607", 8))
    owner = store.acquire(
        lookup=lookup(current, retained),
        now=NOW, lease_seconds=30,
    )
    store.bind(
        identity=retained, fencing_token=owner.fencing_token or 0, receipt=receipt(),
        operation_id="op", outbox_state="pending", dispatch_state="not_dispatched",
        reservation_id="res", max_reserved_cost="1", now=NOW,
        provider_operation_id="provider-op", cost_policy_version="cost-v1",
        cost_policy_digest="d" * 64, settlement_state="reserved",
        reconciliation_state="pending",
    )
    assert store.acquire(
        lookup=lookup(current, retained),
        now=datetime(2026, 9, 1, tzinfo=timezone.utc), lease_seconds=30,
    ).kind == "replay"


def test_terminal_failure_replays_exact_safe_response(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 9))
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    response = TerminalSafeResponse(
        status=409,
        code="relocalization_unavailable",
        schema_version="error/v1",
        body={"ok": False, "error": {"code": "relocalization_unavailable", "message": "Unavailable."}},
        replay_headers={"Retry-After": "3"},
    )
    store.fail_terminal(
        identity=ident, fencing_token=owner.fencing_token or 0, response=response,
        now=NOW, expires_at=NOW + timedelta(hours=24),
    )
    replay = store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW + timedelta(hours=1), lease_seconds=30,
    )
    assert replay.kind == "terminal_replay"
    assert replay.terminal_response == response
    with pytest.raises(ValueError):
        TerminalSafeResponse(
            status=409, code="x", schema_version="error/v1", body={},
            replay_headers={"Set-Cookie": "secret"},
        )


def test_bound_request_can_fail_terminal_before_dispatch(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 27))
    owner = store.acquire(
        lookup=lookup(parsed, ident), now=NOW, lease_seconds=30
    )
    bind_chargeable(store, ident, owner.fencing_token or 0, suffix="_bound_failure")
    response = TerminalSafeResponse(
        status=503,
        code="provider_unavailable",
        schema_version="error/v1",
        body={"ok": False, "error": {"code": "provider_unavailable"}},
        replay_headers={"Retry-After": "3"},
    )
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=response,
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    replay = store.acquire(
        lookup=lookup(parsed, ident), now=NOW + timedelta(minutes=1), lease_seconds=30
    )
    assert replay.kind == "terminal_replay"
    assert replay.terminal_response == response
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - verify durable settlement
        row = db.execute(
            """SELECT outbox_state, dispatch_state, reservation_id, settlement_state,
                      reconciliation_state
               FROM formal_run_idempotency"""
        ).fetchone()
    assert row == ("cancelled", "not_dispatched", "res_bound_failure", "released", "reconciled")
    with pytest.raises(IdempotencyInProgress):
        store.claim_dispatch(
            identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
        )


def test_possibly_dispatched_request_cannot_fail_terminal(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 28))
    owner = store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
    bind_chargeable(store, ident, owner.fencing_token or 0, suffix="_claimed")
    store.claim_dispatch(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    )
    response = TerminalSafeResponse(
        status=503,
        code="provider_unavailable",
        schema_version="error/v1",
        body={"ok": False},
        replay_headers={},
    )
    with pytest.raises(StaleFencingToken, match="already have been dispatched"):
        store.fail_terminal(
            identity=ident,
            fencing_token=owner.fencing_token or 0,
            response=response,
            now=NOW,
            expires_at=NOW + timedelta(hours=24),
        )
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - verify durable authority
        row = db.execute(
            """SELECT state, outbox_state, dispatch_state, settlement_state,
                      reconciliation_state
               FROM formal_run_idempotency"""
        ).fetchone()
    assert row == ("bound", "claimed", "possibly_dispatched", "reserved", "pending")
    store.mark_execution_uncertain(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    )
    uncertain = store.acquire(
        lookup=lookup(parsed, ident), now=NOW + timedelta(minutes=1), lease_seconds=30
    )
    assert uncertain.kind == "replay"
    assert uncertain.receipt is not None
    assert uncertain.receipt.state == "execution_uncertain"


def test_execution_uncertain_replays_without_new_owner(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 10))
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    store.bind(
        identity=ident, fencing_token=owner.fencing_token or 0, receipt=receipt(),
        operation_id="op", outbox_state="pending", dispatch_state="not_dispatched",
        reservation_id="res", max_reserved_cost="1", now=NOW,
        provider_operation_id="provider-op", cost_policy_version="cost-v1",
        cost_policy_digest="d" * 64, settlement_state="reserved",
        reconciliation_state="pending",
    )
    assert store.claim_dispatch(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    ) == "provider-op"
    store.mark_execution_uncertain(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    )
    replay = store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW + timedelta(days=1), lease_seconds=30,
    )
    assert replay.kind == "replay"
    assert replay.receipt == receipt(state="execution_uncertain")


@pytest.mark.parametrize(
    ("disposition", "outbox", "dispatch", "reservation", "maximum"),
    [
        ("created", "none", "not_dispatched", None, None),
        ("fresh-created", "pending", "not_dispatched", None, "1"),
        ("reused", "pending", "not_dispatched", "res", "1"),
        ("relocalized", "none", "dispatched", None, None),
    ],
)
def test_bind_rejects_disposition_cost_invariant_violations(
    tmp_path, disposition, outbox, dispatch, reservation, maximum
):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 20))
    owner = store.acquire(
        lookup=lookup(parsed, ident),
        now=NOW, lease_seconds=30,
    )
    with pytest.raises(ValueError):
        store.bind(
            identity=ident, fencing_token=owner.fencing_token or 0,
            receipt=receipt(disposition=disposition), operation_id="op",
            outbox_state=outbox, dispatch_state=dispatch,
            reservation_id=reservation, max_reserved_cost=maximum, now=NOW,
        )


def test_provider_free_bind_has_no_reservation_or_dispatch(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 21))
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    reused = receipt(disposition="reused")
    store.bind(
        identity=ident, fencing_token=owner.fencing_token or 0, receipt=reused,
        operation_id="op", outbox_state="none", dispatch_state="not_dispatched",
        reservation_id=None, max_reserved_cost=None, now=NOW,
    )
    assert store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW, lease_seconds=30,
    ).receipt == reused


def test_terminal_safe_response_defensively_freezes_body_and_headers():
    body = {"ok": False, "error": {"code": "safe", "items": ["a"]}}
    headers = {"Retry-After": "2"}
    response = TerminalSafeResponse(
        status=409, code="safe", schema_version="error/v1", body=body, replay_headers=headers
    )
    digest = response.digest()
    body["error"]["code"] = "mutated"
    headers["Retry-After"] = "999"
    assert response.digest() == digest
    assert response.replay_headers["Retry-After"] == "2"
    with pytest.raises(TypeError):
        response.body["new"] = "value"  # type: ignore[index]
    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite JSON numbers"):
            TerminalSafeResponse(
                status=500,
                code="safe",
                schema_version="error/v1",
                body={"value": non_finite},
                replay_headers={},
            )
    with pytest.raises(ValueError, match="object keys must be strings"):
        TerminalSafeResponse(
            status=500,
            code="safe",
            schema_version="error/v1",
            body={1: "numeric", "1": "string"},  # type: ignore[dict-item]
            replay_headers={},
        )


def test_expired_terminal_receipt_becomes_tombstone_and_cannot_reopen(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 11))
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409,
            code="safe_failure",
            schema_version="error/v1",
            body={"ok": False, "error": {"code": "safe_failure"}},
            replay_headers={},
        ),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    assert store.acquire(
        lookup=lookup(parsed, ident, fp),
        now=NOW + timedelta(days=2), lease_seconds=30,
    ).kind == "key_unavailable"


def test_finite_tombstone_waits_for_closed_epoch_before_atomic_removal(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 29))
    owner = store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409,
            code="safe",
            schema_version="error/v1",
            body={"ok": False},
            replay_headers={},
        ),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.tombstone(
        identity=ident,
        now=NOW + timedelta(days=1),
        retain_until=NOW + timedelta(days=2),
    )
    assert store.acquire(
        lookup=lookup(parsed, ident),
        now=NOW + timedelta(days=1, hours=1),
        lease_seconds=30,
    ).kind == "key_unavailable"
    still_used = store.acquire(
        lookup=lookup(parsed, ident),
        now=NOW + timedelta(days=2),
        lease_seconds=30,
    )
    assert still_used.kind == "key_unavailable"
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - verify transactional GC
        assert db.execute(
            "SELECT COUNT(*) FROM formal_run_idempotency_tombstone"
        ).fetchone()[0] == 1
    closed_epoch = store.acquire(
        lookup=lookup(parsed, ident),
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        lease_seconds=30,
    )
    assert closed_epoch.kind == "key_unavailable"
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - verify transactional GC
        assert db.execute(
            "SELECT COUNT(*) FROM formal_run_idempotency_tombstone"
        ).fetchone()[0] == 0


def test_corrupt_tombstone_epoch_fails_closed(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 30))
    owner = store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.tombstone(
        identity=ident,
        now=NOW + timedelta(days=1),
        retain_until=NOW + timedelta(days=30),
    )
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - corruption injection
        db.execute(
            "UPDATE formal_run_idempotency_tombstone SET key_epoch='202606'"
        )
    with pytest.raises(IdempotencyUnavailable, match="tombstone epoch"):
        store.acquire(
            lookup=lookup(parsed, ident),
            now=NOW + timedelta(days=31),
            lease_seconds=30,
        )


def test_tombstone_rejects_nonterminal_record(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 22))
    store.acquire(
        lookup=lookup(parsed, ident),
        now=NOW,
        lease_seconds=30,
    )
    with pytest.raises(ValueError, match="expired terminal"):
        store.tombstone(identity=ident, now=NOW, retain_until=NOW + timedelta(days=30))


def test_two_store_instances_racing_same_key_have_one_owner(tmp_path):
    path = tmp_path / "store.db"
    first_store = SqliteFormalRunIdempotencyStore(path, environment="test")
    second_store = SqliteFormalRunIdempotencyStore(path, environment="test")
    parsed, ident = identity(key("202607", 12))

    def acquire(store):
        return store.acquire(
            lookup=lookup(parsed, ident),
            now=NOW, lease_seconds=30,
        ).kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, (first_store, second_store)))
    assert sorted(results) == ["in_progress", "owner"]


def test_corrupt_unknown_state_fails_closed(tmp_path):
    path = tmp_path / "store.db"
    store = SqliteFormalRunIdempotencyStore(path, environment="test")
    parsed, ident = identity(key("202607", 13))
    fp = fingerprint()
    store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE formal_run_idempotency SET state='mystery', lease_expires_at=NULL")
    with pytest.raises(IdempotencyUnavailable, match="unknown"):
        store.acquire(
            lookup=lookup(parsed, ident, fp),
            now=NOW + timedelta(seconds=31), lease_seconds=30,
        )


def test_corrupt_bound_receipt_fails_closed(tmp_path):
    path = tmp_path / "store.db"
    store = SqliteFormalRunIdempotencyStore(path, environment="test")
    parsed, ident = identity(key("202607", 14))
    fp = fingerprint()
    owner = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30
    )
    store.bind(
        identity=ident, fencing_token=owner.fencing_token or 0, receipt=receipt(),
        operation_id="op-corrupt", outbox_state="pending", dispatch_state="not_dispatched",
        reservation_id="res-corrupt", max_reserved_cost="1", now=NOW,
        provider_operation_id="provider-corrupt", cost_policy_version="cost-v1",
        cost_policy_digest="d" * 64, settlement_state="reserved",
        reconciliation_state="pending",
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE formal_run_idempotency SET receipt_body='{}'")
    with pytest.raises(IdempotencyUnavailable, match="receipt"):
        store.acquire(
            lookup=lookup(parsed, ident, fp),
            now=NOW, lease_seconds=30,
        )


@pytest.mark.parametrize("rotate", ("caller", "key", "fingerprint"))
def test_rotation_candidates_replay_retained_authority_without_new_owner(tmp_path, rotate):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed = parse_idempotency_key(key("202607", 30))
    old_identity = versioned_identity(
        parsed, caller_key_id="caller-old", caller_secret=b"o" * 32,
        key_id="key-old", key_secret=b"p" * 32,
    )
    old_fp = request_fingerprint(
        b"q" * 32, "fp-old", coin="BTC", mode="risk", question="Assess risk",
        locale="zh-Hant",
    )
    owner = store.acquire(
        lookup=lookup(parsed, old_identity, old_fp), now=NOW, lease_seconds=30
    )
    expected = bind_chargeable(store, old_identity, owner.fencing_token or 0, suffix="-old")

    new_identity = versioned_identity(
        parsed,
        caller_key_id="caller-new" if rotate == "caller" else "caller-old",
        caller_secret=b"n" * 32 if rotate == "caller" else b"o" * 32,
        key_id="key-new" if rotate == "key" else "key-old",
        key_secret=b"m" * 32 if rotate == "key" else b"p" * 32,
    )
    new_fp = request_fingerprint(
        b"r" * 32 if rotate == "fingerprint" else b"q" * 32,
        "fp-new" if rotate == "fingerprint" else "fp-old",
        coin="BTC", mode="risk", question="Assess risk", locale="zh-Hant",
    )
    replay = store.acquire(
        lookup=lookup(
            parsed, new_identity, new_fp,
            candidate_identities=(old_identity,) if rotate != "fingerprint" else (),
            candidate_fingerprints=(old_fp,) if rotate == "fingerprint" else (),
        ),
        now=NOW + timedelta(days=1), lease_seconds=30,
    )
    assert replay.kind == "replay"
    assert replay.receipt == expected


def test_rotation_candidate_wrong_payload_conflicts_and_never_takes_owner(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed = parse_idempotency_key(key("202607", 31))
    old_identity = versioned_identity(
        parsed, caller_key_id="caller-old", caller_secret=b"o" * 32,
        key_id="key-old", key_secret=b"p" * 32,
    )
    old_fp = request_fingerprint(
        b"q" * 32, "fp-old", coin="BTC", mode="risk", question="Assess risk",
        locale="zh-Hant",
    )
    store.acquire(lookup=lookup(parsed, old_identity, old_fp), now=NOW, lease_seconds=1)
    new_identity = versioned_identity(
        parsed, caller_key_id="caller-new", caller_secret=b"n" * 32,
        key_id="key-new", key_secret=b"m" * 32,
    )
    wrong_old_fp = request_fingerprint(
        b"q" * 32, "fp-old", coin="BTC", mode="risk", question="Different",
        locale="zh-Hant",
    )
    rotated = lookup(
        parsed, new_identity,
        request_fingerprint(
            b"r" * 32, "fp-new", coin="BTC", mode="risk", question="Different",
            locale="zh-Hant",
        ),
        candidate_identities=(old_identity,), candidate_fingerprints=(wrong_old_fp,),
    )
    assert store.acquire(
        lookup=rotated, now=NOW + timedelta(seconds=2), lease_seconds=30
    ).kind == "conflict"
    exact = lookup(
        parsed, new_identity,
        request_fingerprint(
            b"r" * 32, "fp-new", coin="BTC", mode="risk", question="Assess risk",
            locale="zh-Hant",
        ),
        candidate_identities=(old_identity,), candidate_fingerprints=(old_fp,),
    )
    recovered = store.acquire(
        lookup=exact, now=NOW + timedelta(seconds=2), lease_seconds=30
    )
    assert recovered.kind == "owner"
    assert recovered.fencing_token == 2
    assert recovered.authority_identity == old_identity
    with pytest.raises(StaleFencingToken):
        bind_chargeable(store, old_identity, 1, suffix="-stale")
    bind_chargeable(
        store, recovered.authority_identity, recovered.fencing_token or 0, suffix="-recovered"
    )


def test_rotated_lookup_honors_legacy_tombstone(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, old_identity = identity(key("202607", 32))
    fp = fingerprint()
    owner = store.acquire(lookup=lookup(parsed, old_identity, fp), now=NOW, lease_seconds=30)
    store.fail_terminal(
        identity=old_identity, fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409, code="safe_failure", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW, expires_at=NOW + timedelta(days=1),
    )
    new_identity = versioned_identity(
        parsed, caller_key_id="caller-new", caller_secret=b"n" * 32,
        key_id="key-new", key_secret=b"m" * 32,
    )
    rotated = lookup(
        parsed, new_identity,
        request_fingerprint(
            b"r" * 32, "fp-new", coin="BTC", mode="risk", question="Assess risk",
            locale="zh-Hant",
        ),
        candidate_identities=(old_identity,), candidate_fingerprints=(fp,),
    )
    assert store.acquire(
        lookup=rotated, now=NOW + timedelta(days=2), lease_seconds=30
    ).kind == "key_unavailable"
    assert store.acquire(
        lookup=rotated, now=NOW + timedelta(days=3), lease_seconds=30
    ).kind == "key_unavailable"


def test_dispatch_claim_is_atomic_and_uncertain_preserves_receipt_identity(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 33))
    fp = fingerprint()
    owner = store.acquire(lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30)
    original = bind_chargeable(store, ident, owner.fencing_token or 0, suffix="-dispatch")
    assert store.claim_dispatch(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    ) == "provider-dispatch"
    with pytest.raises(IdempotencyInProgress):
        store.claim_dispatch(
            identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
        )
    store.mark_execution_uncertain(
        identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
    )
    replay = store.acquire(
        lookup=lookup(parsed, ident, fp), now=NOW + timedelta(days=1), lease_seconds=30
    )
    assert replay.receipt == FormalRunReceipt(**{**original.public_body(), "state": "execution_uncertain"})


def test_provider_free_receipt_cannot_claim_dispatch(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 34))
    owner = store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
    store.bind(
        identity=ident, fencing_token=owner.fencing_token or 0,
        receipt=receipt(disposition="reused"), operation_id="op-provider-free",
        outbox_state="none", dispatch_state="not_dispatched",
        reservation_id=None, max_reserved_cost=None, now=NOW,
    )
    with pytest.raises(ValueError):
        store.claim_dispatch(
            identity=ident, fencing_token=owner.fencing_token or 0, now=NOW
        )


def test_duplicate_chargeable_ids_in_same_scope_are_rejected(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    first_key, first_identity = identity(key("202607", 35))
    second_key, second_identity = identity(key("202607", 36))
    first = store.acquire(lookup=lookup(first_key, first_identity), now=NOW, lease_seconds=30)
    bind_chargeable(store, first_identity, first.fencing_token or 0, suffix="-duplicate")
    second = store.acquire(lookup=lookup(second_key, second_identity), now=NOW, lease_seconds=30)
    with pytest.raises(ValueError, match="already bound"):
        bind_chargeable(store, second_identity, second.fencing_token or 0, suffix="-duplicate")


def test_caller_hmac_rotation_cannot_bypass_scoped_unique_ids(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    first_key = parse_idempotency_key(key("202607", 37))
    second_key = parse_idempotency_key(key("202607", 38))
    before = versioned_identity(
        first_key, caller_key_id="caller-old", caller_secret=b"o" * 32,
        key_id="key-v1", key_secret=b"k" * 32,
    )
    after = versioned_identity(
        second_key, caller_key_id="caller-new", caller_secret=b"n" * 32,
        key_id="key-v1", key_secret=b"k" * 32,
    )
    assert before.scope_locator == after.scope_locator
    first = store.acquire(lookup=lookup(first_key, before), now=NOW, lease_seconds=30)
    bind_chargeable(store, before, first.fencing_token or 0, suffix="-rotation-duplicate")
    second = store.acquire(lookup=lookup(second_key, after), now=NOW, lease_seconds=30)
    with pytest.raises(ValueError, match="already bound"):
        bind_chargeable(store, after, second.fencing_token or 0, suffix="-rotation-duplicate")


def test_provider_free_reuse_may_share_existing_job_id(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    first_key, first_identity = identity(key("202607", 39))
    first = store.acquire(lookup=lookup(first_key, first_identity), now=NOW, lease_seconds=30)
    created = bind_chargeable(store, first_identity, first.fencing_token or 0, suffix="-shared-job")

    second_key, second_identity = identity(key("202607", 40))
    second = store.acquire(lookup=lookup(second_key, second_identity), now=NOW, lease_seconds=30)
    reused = FormalRunReceipt(
        receipt_id="frc_reused",
        question_id="q_reused",
        job_id=created.job_id,
        result_id=created.result_id,
        state="accepted",
        origin="manual",
        disposition="reused",
        locale="zh-Hant",
        created_at="2026-07-30T08:00:01Z",
    )
    store.bind(
        identity=second_identity,
        fencing_token=second.fencing_token or 0,
        receipt=reused,
        operation_id="op_reused",
        outbox_state="none",
        dispatch_state="not_dispatched",
        reservation_id=None,
        max_reserved_cost=None,
        now=NOW,
    )
    assert store.acquire(
        lookup=lookup(second_key, second_identity), now=NOW, lease_seconds=30
    ).receipt == reused


def test_nonterminal_receipt_rejects_expiry_and_parsed_key_constructor_is_canonical():
    with pytest.raises(ValueError, match="must not expire"):
        FormalRunReceipt(
            **{**receipt().public_body(), "expires_at": "2026-07-31T08:00:00Z"}
        )
    with pytest.raises(ValueError, match="parsed idempotency"):
        ParsedIdempotencyKey(raw="tf1.202613.AAAAAAAAAAAAAAAAAAAAAA", epoch="202613")


@pytest.mark.parametrize(
    "column",
    [
        "terminal_error_code",
        "terminal_http_status",
        "terminal_response_schema_version",
        "terminal_safe_response_body",
        "terminal_replay_headers",
        "terminal_response_digest",
        "terminal_at",
        "expires_at",
        "disposition",
    ],
)
def test_incomplete_terminal_record_fails_closed(tmp_path, column):
    store = SqliteFormalRunIdempotencyStore(tmp_path / f"{column}.db", environment="test")
    parsed, ident = identity(key("202607", 41))
    fp = fingerprint()
    owner = store.acquire(lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30)
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - corruption injection
        if column == "disposition":
            db.execute("UPDATE formal_run_idempotency SET disposition='corrupt'")
        else:
            db.execute(f"UPDATE formal_run_idempotency SET {column}=NULL")
    with pytest.raises(IdempotencyUnavailable, match="terminal"):
        store.acquire(lookup=lookup(parsed, ident, fp), now=NOW, lease_seconds=30)


def test_prebind_terminal_with_residual_authority_fails_closed(tmp_path):
    store = SqliteFormalRunIdempotencyStore(tmp_path / "store.db", environment="test")
    parsed, ident = identity(key("202607", 42))
    owner = store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
    store.fail_terminal(
        identity=ident,
        fencing_token=owner.fencing_token or 0,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    with sqlite3.connect(store._path) as db:  # noqa: SLF001 - corruption injection
        db.execute(
            "UPDATE formal_run_idempotency SET operation_id='residual-operation'"
        )
    with pytest.raises(IdempotencyUnavailable, match="pre-bind terminal authority"):
        store.acquire(lookup=lookup(parsed, ident), now=NOW, lease_seconds=30)
