from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor

import pytest

from trustforge.analysis_flow import AnalysisFlow
from trustforge.formal_run_idempotency import build_identity, parse_idempotency_key

def identity(byte: int = 1, *, caller: str = "tenant-a"):
    random_part = base64.urlsafe_b64encode(bytes([byte]) * 16).decode().rstrip("=")
    parsed = parse_idempotency_key(f"tf1.202607.{random_part}")
    return build_identity(
        namespace="formal-analysis", caller_scope=caller, parsed_key=parsed,
        caller_secret=b"c" * 32, caller_key_id="caller-v1",
        idempotency_secret=b"k" * 32, idempotency_key_id="key-v1",
        retention_locator_secret=b"l" * 32,
    )


def test_formal_plan_is_provider_free_and_deterministic(tmp_path, monkeypatch):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    monkeypatch.setattr(
        "trustforge.analysis_flow.collect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not collect")),
    )
    first = flow.plan_formal_manual(
        " btc ", "risk", "Assess risk", locale="zh-Hant",
        fresh=False, operation_id="op-stable", identity=ident,
    )
    second = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        fresh=False, operation_id="op-stable", identity=ident,
    )
    assert first == second
    assert first == {
        "disposition": "created",
        "question_id": first["question_id"],
        "job_id": first["job_id"],
        "result_id": f"result-{first['job_id']}",
    }


def test_fresh_plan_bypasses_existing_content_without_collect(tmp_path, monkeypatch):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *_args, **_kwargs: [])
    flow.submit_manual("BTC", "risk", "Assess risk", locale="zh-Hant")
    monkeypatch.setattr(
        "trustforge.analysis_flow.collect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not collect")),
    )
    planned = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        fresh=True, operation_id="op-fresh", identity=ident,
    )
    assert planned["disposition"] == "fresh-created"


def test_legacy_content_never_returns_private_ids_to_formal_scope(tmp_path, monkeypatch):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *_args, **_kwargs: [])
    legacy_question, legacy_job = flow.submit_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant"
    )
    planned = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id="op-formal", identity=ident,
    )
    assert planned["disposition"] == "created"
    assert planned["question_id"] != legacy_question
    assert planned["job_id"] != legacy_job


def test_different_operation_locale_change_is_independently_created(tmp_path, monkeypatch):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    original = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id="op-original", identity=ident,
    )
    flow.enqueue_formal_projection(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        job_id=original["job_id"], operation_id="op-original",
        identity=ident, fencing_token=1,
    )
    planned = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="en",
        fresh=False, operation_id="op-locale",
        identity=ident,
    )
    assert planned["disposition"] == "created"
    assert planned["job_id"] != original["job_id"]


def test_projection_enqueue_is_restart_durable_and_never_collects(tmp_path, monkeypatch):
    path = tmp_path / "flow.sqlite3"
    flow = AnalysisFlow(path)
    operation = "op-deterministic"
    ident = identity()
    job_id = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )["job_id"]
    monkeypatch.setattr(
        "trustforge.analysis_flow.collect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not collect")),
    )
    assert flow.enqueue_formal_projection(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        job_id=job_id, operation_id=operation,
        identity=ident, fencing_token=1,
    )[1] == job_id
    flow.close()
    restarted = AnalysisFlow(path)
    row = restarted._conn().execute(  # noqa: SLF001
        "SELECT * FROM formal_analysis_projection_queue WHERE operation_id=?",
        (operation,),
    ).fetchone()
    assert row["state"] == "pending"
    assert row["job_id"] == job_id
    legacy = restarted._conn().execute(  # noqa: SLF001
        """SELECT question_id FROM analysis_questions
           WHERE coin='BTC' AND mode='risk' AND question='Assess risk'"""
    ).fetchone()
    assert legacy is None
    assert row["question_id"].startswith("question-")
    restarted.enqueue_formal_projection(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        job_id=job_id, operation_id=operation,
        identity=ident, fencing_token=1,
    )


def test_formal_plans_are_independent_across_operation_and_scope(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    operation = "op-scope-a"
    planned = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )
    flow.enqueue_formal_projection(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        job_id=planned["job_id"], operation_id=operation,
        identity=ident, fencing_token=1,
    )
    same = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id="op-same", identity=ident,
    )
    other_identity = identity(2, caller="tenant-b")
    other = flow.plan_formal_manual(
        "BTC", "risk", "Assess risk", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=other_identity,
    )
    assert same["disposition"] == "created"
    assert same["job_id"] != planned["job_id"]
    assert other["disposition"] == "created"
    assert other["job_id"] != planned["job_id"]
    assert other["question_id"] != planned["question_id"]
    flow.enqueue_formal_projection(
        "BTC", "risk", "Assess risk", locale="zh-Hant",
        job_id=other["job_id"], operation_id=operation,
        identity=other_identity, fencing_token=1,
    )
    assert flow._conn().execute(  # noqa: SLF001
        """SELECT count(*) FROM formal_analysis_projection_queue
           WHERE operation_id=?""",
        (operation,),
    ).fetchone()[0] == 2


def test_parallel_different_operations_do_not_claim_local_content_dedup(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()

    def plan(index: int):
        return flow.plan_formal_manual(
            "BTC", "risk", "Same content", locale="zh-Hant", fresh=False,
            operation_id=f"op-parallel-{index}", identity=ident,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        planned = list(pool.map(plan, range(8)))
    assert {item["disposition"] for item in planned} == {"created"}
    assert len({item["job_id"] for item in planned}) == 8


@pytest.mark.parametrize(
    ("operation", "job_id", "fence"),
    [
        ("bad operation", "flow-deadbeefdeadbeef", 1),
        ("op-valid", "flow-attackerchosen", 1),
        ("op-valid", "$expected", 0),
        ("op-valid", "$expected", True),
        ("op-valid", "$expected", 9_223_372_036_854_775_808),
    ],
)
def test_projection_validation_precedes_question_side_effect(
    tmp_path, operation, job_id, fence,
):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    if job_id == "$expected":
        job_id = flow.plan_formal_manual(
            "BTC", "risk", "Must not persist", locale="zh-Hant", fresh=False,
            operation_id=operation, identity=ident,
        )["job_id"]
    with pytest.raises((ValueError, TypeError)):
        flow.enqueue_formal_projection(
            "BTC", "risk", "Must not persist", locale="zh-Hant",
            job_id=job_id, operation_id=operation, identity=ident,
            fencing_token=fence,
        )
    assert flow._conn().execute(  # noqa: SLF001
        "SELECT count(*) FROM analysis_questions WHERE question='Must not persist'"
    ).fetchone()[0] == 0


def test_projection_rejects_arbitrary_authority_before_side_effect(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    operation = "op-authority"
    ident = identity()
    job_id = flow.plan_formal_manual(
        "BTC", "risk", "Must not persist", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )["job_id"]
    with pytest.raises(TypeError, match="validated formal-run"):
        flow.enqueue_formal_projection(
            "BTC", "risk", "Must not persist", locale="zh-Hant",
            job_id=job_id, operation_id=operation,
            identity={"scope_locator": "a" * 64},  # type: ignore[arg-type]
            fencing_token=1,
        )
    assert flow._conn().execute(  # noqa: SLF001
        "SELECT count(*) FROM analysis_questions WHERE question='Must not persist'"
    ).fetchone()[0] == 0


def test_planner_rejects_untrusted_scope_shaped_input(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    with pytest.raises(TypeError, match="validated formal-run"):
        flow.plan_formal_manual(
            "BTC", "risk", "Must not inspect", locale="zh-Hant", fresh=False,
            operation_id="op-untrusted",
            identity={"namespace": "formal-analysis", "scope_locator": "a" * 64},  # type: ignore[arg-type]
        )


def test_projection_schema_contains_only_typed_authority_references(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    columns = {
        row["name"]: row["type"]
        for row in flow._conn().execute(  # noqa: SLF001
            "PRAGMA table_info(formal_analysis_projection_queue)"
        ).fetchall()
    }
    assert "authority_json" not in columns
    assert {
        "namespace", "scope_locator", "caller_key_id", "caller_scope_hmac",
        "key_key_id", "key_hmac", "fencing_token",
    } <= columns.keys()
    assert columns["fencing_token"] == "INTEGER"


def test_existing_operation_conflict_rolls_back_new_question(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    ident = identity()
    operation = "op-conflict"
    job_id = flow.plan_formal_manual(
        "BTC", "risk", "Original", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )["job_id"]
    flow.enqueue_formal_projection(
        "BTC", "risk", "Original", locale="zh-Hant", job_id=job_id,
        operation_id=operation, identity=ident, fencing_token=1,
    )
    with pytest.raises(Exception, match="conflicts"):
        flow.enqueue_formal_projection(
            "BTC", "risk", "Injected", locale="zh-Hant", job_id=job_id,
            operation_id=operation, identity=ident, fencing_token=1,
        )
    assert flow._conn().execute(  # noqa: SLF001
        "SELECT count(*) FROM analysis_questions WHERE question='Injected'"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "state", ("pending", "claiming", "collecting", "completed", "execution_uncertain")
)
def test_same_immutable_intent_replay_preserves_every_valid_state(tmp_path, state):
    flow = AnalysisFlow(tmp_path / f"{state}.sqlite3")
    ident = identity()
    operation = f"op-{state}"
    plan = flow.plan_formal_manual(
        "BTC", "risk", "Immutable", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )
    expected = flow.enqueue_formal_projection(
        "BTC", "risk", "Immutable", locale="zh-Hant",
        job_id=plan["job_id"], operation_id=operation,
        identity=ident, fencing_token=7,
    )
    flow._conn().execute(  # noqa: SLF001
        """UPDATE formal_analysis_projection_queue SET state=?
           WHERE namespace=? AND scope_locator=? AND operation_id=?""",
        (state, ident.namespace, ident.scope_locator, operation),
    )
    assert flow.enqueue_formal_projection(
        "BTC", "risk", "Immutable", locale="zh-Hant",
        job_id=plan["job_id"], operation_id=operation,
        identity=ident, fencing_token=7,
    ) == expected
    observed = flow._conn().execute(  # noqa: SLF001
        """SELECT state FROM formal_analysis_projection_queue
           WHERE namespace=? AND scope_locator=? AND operation_id=?""",
        (ident.namespace, ident.scope_locator, operation),
    ).fetchone()
    assert observed["state"] == state
    independent = flow.plan_formal_manual(
        "BTC", "risk", "Immutable", locale="zh-Hant", fresh=False,
        operation_id=f"op-content-replay-{state}", identity=ident,
    )
    assert independent["disposition"] == "created"
    assert independent["job_id"] != plan["job_id"]


def test_formal_queue_does_not_consume_or_depend_on_legacy_question_quota(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    for index in range(20):
        flow.register_question(
            "BTC", "risk", f"legacy-{index}", enqueue=False
        )
    ident = identity()
    operation = "op-after-legacy-quota"
    plan = flow.plan_formal_manual(
        "BTC", "risk", "Formal remains available", locale="zh-Hant", fresh=False,
        operation_id=operation, identity=ident,
    )
    flow.enqueue_formal_projection(
        "BTC", "risk", "Formal remains available", locale="zh-Hant",
        job_id=plan["job_id"], operation_id=operation,
        identity=ident, fencing_token=1,
    )
    assert flow._conn().execute(  # noqa: SLF001
        "SELECT count(*) FROM analysis_questions"
    ).fetchone()[0] == 20
    assert flow._conn().execute(  # noqa: SLF001
        "SELECT count(*) FROM formal_analysis_projection_queue"
    ).fetchone()[0] == 1
