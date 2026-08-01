#!/usr/bin/env python3
"""Ask the configured Bedrock LLM to adversarially review upgrade candidates."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge import budget_guard, ledger as ledger_module  # noqa: E402
from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402
from trustforge.ledger import estimate_cost  # noqa: E402
from trustforge.schema import iso_utc  # noqa: E402
from trustforge.upgrade_queue import UpgradeQueue  # noqa: E402
from trustforge.upgrade_review import review  # noqa: E402

_LOG = logging.getLogger(__name__)
_MAX_PROPOSALS = 20
_MAX_FIELD_BYTES = 4_096
_MAX_REVIEW_INPUT_BYTES = 32_768
_MAX_REVIEW_OUTPUT_TOKENS = 1_024


class _ReviewBlocked(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _blocked_result(status: str, reason: str) -> dict:
    return {
        "status": status,
        "reason": reason,
        "reviews": [],
        "can_activate": False,
    }


def _persist_review_outcome(result: dict, path: Path | None = None) -> dict:
    """Append one fsync'd audit outcome without relying on replaceable latest state."""
    destination = path or (REPO / "out" / "hermes-upgrade-review-runs.jsonl")
    destination.parent.mkdir(parents=True, exist_ok=True)
    outcome = {
        "run_id": str(uuid.uuid4()),
        "ts": iso_utc(time.time()),
        **result,
    }
    payload = (json.dumps(outcome, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("review outcome append made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return outcome


def _budgeted_complete(
    client: BedrockClient,
    system: str,
    prompt: str,
    *,
    now_fn=time.time,
) -> str:
    """Run one reviewer call under the canonical atomic budget authority.

    Accounting deliberately completes before the reservation is released. A
    shared reservation is retained when no durable ledger receipt exists, so a
    DynamoDB outage cannot reopen capacity which may already have been spent.
    """
    if len(system.encode("utf-8")) + len(prompt.encode("utf-8")) > _MAX_REVIEW_INPUT_BYTES:
        raise _ReviewBlocked("input_too_large", "review_prompt_exceeds_byte_limit")
    if not budget_guard.narrative_model_priced():
        raise _ReviewBlocked("unpriced_model", "bedrock_model_is_not_priced")

    try:
        reservation_backend = budget_guard.budget_reservation_backend()
        reservation = budget_guard.try_reserve_request_budget(
            backend=reservation_backend
        )
    except Exception as exc:
        _LOG.warning("Hermes reviewer budget reservation failed", exc_info=True)
        raise _ReviewBlocked("budget_unavailable", "budget_reservation_failed") from exc
    reservation_value = float(reservation) if reservation is not None else 0.0
    if not math.isfinite(reservation_value) or reservation_value <= 0:
        raise _ReviewBlocked("budget_denied", "budget_reservation_denied")

    unified_reservation = budget_guard.reservation_is_durable_shared(reservation)
    shared_reservation = unified_reservation or reservation_backend == "dynamodb"
    release_safe = False
    accounting_finalized = False
    try:
        try:
            response = client.complete(system=system, prompt=prompt)
        except Exception as exc:
            # A provider exception can arrive after Bedrock accepted the work.
            # Charge the conservative reservation before releasing local
            # capacity; shared capacity remains held for reconciliation.
            try:
                budget_guard.record_unledgered_spend(float(reservation))
                if shared_reservation:
                    budget_guard.mark_reservation_accounting_uncertain(reservation)
                else:
                    release_safe = True
            except Exception:
                _LOG.exception(
                    "Hermes reviewer could not record uncertain Bedrock usage"
                )
            raise _ReviewBlocked("review_failed", "bedrock_call_failed") from exc

        try:
            model_id = str(response.model_id).strip()
            tokens_in = int(response.input_tokens)
            tokens_out = int(response.output_tokens)
            cost_usd = estimate_cost(model_id, tokens_in, tokens_out)
            usage_is_certain = (
                bool(model_id)
                and tokens_in > 0
                and tokens_out > 0
                and math.isfinite(float(cost_usd))
                and float(cost_usd) > 0
            )
        except (TypeError, ValueError, OverflowError):
            usage_is_certain = False
        if not usage_is_certain:
            # The provider may have accepted work even when its accounting
            # metadata is absent or malformed. Conservatively charge local
            # capacity and retain shared capacity for reconciliation.
            budget_guard.record_unledgered_spend(reservation_value)
            if shared_reservation:
                budget_guard.mark_reservation_accounting_uncertain(reservation)
            else:
                release_safe = True
            raise _ReviewBlocked(
                "accounting_failed", "bedrock_usage_metadata_ambiguous"
            )
        ledger_record = {
            "ts": iso_utc(now_fn()),
            "question_type": "hermes_upgrade_review",
            "coin": None,
            "offline": False,
            "calls": [
                {
                    "model": model_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost_usd,
                }
            ],
            "total_cost_usd": cost_usd,
            # Unified settlement owns this spend atomically. Excluding it from
            # daily_nonformal_cost_usd avoids counting the same cost once in
            # the ledger and again in the authority's settled_total.
            "accounting_authority": "formal" if unified_reservation else "legacy",
            "accounting_outcome": "charged",
        }
        try:
            if shared_reservation:
                # ``append_run`` may successfully fall back to process-local
                # JSONL after a DynamoDB failure.  That is useful for ordinary
                # reporting but is not a shared receipt and must never unlock
                # shared reservation capacity.
                durable_ledger = ledger_module.get_ledger()
                if not isinstance(durable_ledger, ledger_module.DynamoDBLedger):
                    persisted = False
                else:
                    durable_ledger.append(ledger_record)
                    persisted = True
            else:
                persisted = ledger_module.append_run(ledger_record)
        except Exception:
            persisted = False
            _LOG.warning("Hermes reviewer ledger append raised unexpectedly", exc_info=True)
        if not persisted:
            budget_guard.record_unledgered_spend(cost_usd)
            if not shared_reservation:
                release_safe = True
            raise _ReviewBlocked("accounting_failed", "durable_ledger_receipt_missing")

        if shared_reservation:
            try:
                accounting_finalized = budget_guard.settle_request_budget(
                    reservation,
                    float(cost_usd),
                    backend=reservation_backend,
                )
            except Exception:
                accounting_finalized = False
                _LOG.warning(
                    "Hermes reviewer atomic budget settlement failed",
                    exc_info=True,
                )
            if not accounting_finalized:
                # The durable ledger receipt remains useful for reporting, but
                # it cannot close the admission race by itself.  Retain shared
                # capacity until reconciliation rather than reopening spend.
                try:
                    budget_guard.mark_reservation_accounting_uncertain(reservation)
                except Exception:
                    _LOG.warning(
                        "Hermes reviewer could not mark retained settlement",
                        exc_info=True,
                    )
        else:
            release_safe = True
        return response.text
    finally:
        if release_safe:
            try:
                budget_guard.release_request_budget(
                    reservation, backend=reservation_backend
                )
            except Exception:
                # Ledger accounting is already durable. Retaining capacity on
                # release failure is conservative and must not duplicate spend.
                _LOG.warning(
                    "Hermes reviewer budget release failed",
                    exc_info=True,
                )
        elif not accounting_finalized:
            _LOG.critical(
                "Hermes reviewer retained reservation for reconciliation (backend=%s)",
                reservation_backend,
            )


def _review_with_budget(
    diagnostic: dict, client: BedrockClient, *, now_fn=time.time
) -> dict:
    proposals = diagnostic.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        return {"status": "no_candidates", "reviews": [], "can_activate": False}
    if len(proposals) > _MAX_PROPOSALS:
        return _blocked_result("input_too_large", "too_many_review_proposals")
    if _contains_oversized_field(diagnostic):
        return _blocked_result("input_too_large", "review_field_exceeds_byte_limit")
    try:
        return review(
            diagnostic,
            lambda system, prompt: _budgeted_complete(
                client, system, prompt, now_fn=now_fn
            ),
        )
    except _ReviewBlocked as exc:
        return _blocked_result(exc.status, exc.reason)
    except Exception:
        _LOG.warning("Hermes upgrade review failed", exc_info=True)
        return _blocked_result("review_failed", "invalid_reviewer_response")


def _contains_oversized_field(value: object) -> bool:
    if isinstance(value, str):
        return len(value.encode("utf-8")) > _MAX_FIELD_BYTES
    if isinstance(value, dict):
        return any(
            _contains_oversized_field(key) or _contains_oversized_field(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_oversized_field(item) for item in value)
    return False


def _bind_diagnostic(
    queue: UpgradeQueue, diagnostic: dict
) -> tuple[dict, dict[str, dict[str, str]]]:
    bound = dict(diagnostic)
    proposals = []
    bindings: dict[str, dict[str, str]] = {}
    for proposal in diagnostic.get("proposals", []):
        if not isinstance(proposal, dict) or not isinstance(proposal.get("id"), str):
            continue
        binding = queue.resolve_exact_review_instance(proposal)
        durable = dict(proposal)
        durable["logical_id"] = binding["logical_id"]
        durable["id"] = binding["proposal_id"]
        durable["payload_sha256"] = binding["payload_sha256"]
        proposals.append(durable)
        bindings[binding["proposal_id"]] = binding
    bound["proposals"] = proposals
    return bound, bindings


def _attach_review_bindings(result: dict, bindings: dict[str, dict[str, str]]) -> dict:
    attached = dict(result)
    reviews = []
    for review_row in result.get("reviews", []):
        if not isinstance(review_row, dict):
            continue
        durable_id = str(review_row.get("proposal_id", ""))
        binding = bindings.get(durable_id)
        if binding is None:
            raise ValueError("review result returned an unbound proposal instance")
        reviews.append({**review_row, "payload_sha256": binding["payload_sha256"]})
    attached["reviews"] = reviews
    return attached


def main() -> int:
    source = REPO / "out" / "hermes-improvement-latest.json"
    target = REPO / "out" / "hermes-upgrade-review-latest.json"
    diagnostic = (
        json.loads(source.read_text(encoding="utf-8"))
        if source.is_file()
        else {"proposals": []}
    )
    queue = UpgradeQueue()
    diagnostic, bindings = _bind_diagnostic(queue, diagnostic)
    config = BedrockConfig()
    config.max_tokens = max(1, min(config.max_tokens, _MAX_REVIEW_OUTPUT_TOKENS))
    if not config.model_id:
        result = {
            "status": "waiting_model_configuration",
            "reviews": [],
            "can_activate": False,
        }
    else:
        client = BedrockClient(config=config, offline=False)
        result = _review_with_budget(diagnostic, client)
        result = _attach_review_bindings(result, bindings)
    _persist_review_outcome(result)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    queue.record_reviews(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
