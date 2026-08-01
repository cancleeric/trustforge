from __future__ import annotations

import json
from pathlib import Path


CONTRACT = json.loads(
    (Path(__file__).parents[1] / "deploy" / "competition-lambda-contract.json").read_text()
)


def test_offline_contract_is_pinned_to_competition_account_and_region():
    assert CONTRACT["account_id"] == "850849012389"
    assert CONTRACT["region"] == "us-east-1"
    assert CONTRACT["function_name"].startswith("competition-trustforge-")
    assert CONTRACT["timeout_seconds"] == 30
    assert CONTRACT["reserved_concurrency"] == 1


def test_offline_contract_has_no_secret_model_or_data_access():
    environment = CONTRACT["environment"]
    assert environment == {
        "TRUSTFORGE_HOME": "/var/task",
        "TRUSTFORGE_COMPETITION_MODE": "offline",
    }
    assert CONTRACT["execution_role"]["allowed_actions"] == [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    ]
    assert CONTRACT["allowed_requests"] == [
        {"method": "GET", "path": "/"},
        {"method": "GET", "path": "/healthz"},
    ]


def test_live_activation_is_blocked_on_distributed_limiter():
    live = CONTRACT["live_activation"]
    assert live["status"] == "blocked-bedrock-distributed-limiter"
    assert live["activation_blockers"] == [
        "Lambda Bedrock invocation remains disabled until a reviewed distributed <=1 RPS limiter is implemented"
    ]
    assert live["contract"] == "deploy/competition-lambda-live-contract.json"
    assert live["region"] == "us-east-1"
    assert live["narrative_model_id"] == live["stance_model_id"]
    assert live["narrative_model_id"].startswith("us.")
    assert live["daily_usd_cap"] == 10
    assert live["required_inference_profile_arn"].startswith(
        "arn:aws:bedrock:us-east-1:850849012389:inference-profile/"
    )
    assert {arn.split(":")[3] for arn in live["required_foundation_model_arns"]} == {
        "us-east-1",
        "us-east-2",
        "us-west-2",
    }


def test_contract_has_required_lifecycle_tags():
    assert set(CONTRACT["tags"]) == {"owner", "purpose", "cost-center", "expiry"}


def test_rotation_runbook_does_not_claim_alias_level_reserved_concurrency():
    runbook = (
        Path(__file__).parents[1]
        / "docs"
        / "competition"
        / "AWS-LAMBDA-DEPLOYMENT.md"
    ).read_text()
    assert "alias- or version-level reserved concurrency" in runbook
    assert "old alias/version reserved concurrency" not in runbook
    assert "old token is rejected through the deployment alias" in runbook
