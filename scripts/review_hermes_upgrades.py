#!/usr/bin/env python3
"""Ask the configured Bedrock LLM to adversarially review upgrade candidates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402
from trustforge.upgrade_review import review  # noqa: E402
from trustforge.upgrade_queue import UpgradeQueue  # noqa: E402


def _bind_diagnostic(queue: UpgradeQueue, diagnostic: dict) -> tuple[dict, dict[str, dict[str, str]]]:
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
    diagnostic = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {"proposals": []}
    queue = UpgradeQueue()
    diagnostic, bindings = _bind_diagnostic(queue, diagnostic)
    config = BedrockConfig()
    if not config.model_id:
        result = {"status": "waiting_model_configuration", "reviews": [], "can_activate": False}
    else:
        client = BedrockClient(config=config, offline=False)
        result = review(diagnostic, lambda system, prompt: client.complete(system=system, prompt=prompt).text)
        result = _attach_review_bindings(result, bindings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    queue.record_reviews(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
