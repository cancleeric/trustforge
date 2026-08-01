#!/usr/bin/env python3
"""Ask the configured Bedrock LLM to adversarially review upgrade candidates."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge import budget_guard  # noqa: E402
from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402
from trustforge.ledger import append_run, estimate_cost  # noqa: E402
from trustforge.schema import iso_utc  # noqa: E402
from trustforge.upgrade_queue import UpgradeQueue  # noqa: E402
from trustforge.upgrade_review import review  # noqa: E402

_LOG = logging.getLogger(__name__)


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
    if reservation is None:
        raise _ReviewBlocked("budget_denied", "budget_reservation_denied")

    shared_reservation = (
        budget_guard.reservation_is_durable_shared(reservation)
        or reservation_backend == "dynamodb"
    )
    release_safe = False
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

        tokens_in = int(response.input_tokens or 0)
        tokens_out = int(response.output_tokens or 0)
        cost_usd = estimate_cost(response.model_id, tokens_in, tokens_out)
        ledger_record = {
            "ts": iso_utc(now_fn()),
            "question_type": "hermes_upgrade_review",
            "coin": None,
            "offline": False,
            "calls": [
                {
                    "model": response.model_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost_usd,
                }
            ],
            "total_cost_usd": cost_usd,
            "accounting_authority": "legacy",
            "accounting_outcome": "charged",
        }
        try:
            persisted = append_run(ledger_record)
        except Exception:
            persisted = False
            _LOG.warning(
                "Hermes reviewer ledger append raised unexpectedly",
                exc_info=True,
            )
        if not persisted:
            budget_guard.record_unledgered_spend(cost_usd)
            if not shared_reservation:
                release_safe = True
            raise _ReviewBlocked("accounting_failed", "durable_ledger_receipt_missing")

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
        else:
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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    queue.record_reviews(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
