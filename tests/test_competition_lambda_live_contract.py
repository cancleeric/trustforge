from __future__ import annotations

import json
from pathlib import Path


CONTRACT = json.loads(
    (Path(__file__).parents[1] / "deploy" / "competition-lambda-live-contract.json").read_text()
)


def test_live_contract_is_exactly_scoped():
    assert CONTRACT["status"] == "owner-authorized-pending-final-ciso-review"
    assert CONTRACT["account_id"] == "850849012389"
    assert CONTRACT["region"] == "us-east-1"
    assert CONTRACT["function_name"] == "competition-trustforge-team11-live"
    assert CONTRACT["reserved_concurrency"] == 1
    assert CONTRACT["timeout_seconds"] == 30
    assert CONTRACT["daily_usd_cap"] == 10


def test_live_contract_has_no_plaintext_or_online_stance_bypass():
    env = CONTRACT["environment"]
    assert env["TRUSTFORGE_COMPETITION_MODE"] == "live"
    assert env["TRUSTFORGE_BUDGET_GUARD_BACKEND"] == "dynamodb"
    assert env["COST_LEDGER_BACKEND"] == "dynamodb"
    assert "TRUSTFORGE_LIVE_TOKEN" not in env
    assert all(name not in env for name in (
        "ARKHAM_API_KEY", "CMC_PRO_API_KEY", "ETHERSCAN_API_KEY", "WHALE_ALERT_API_KEY"
    ))
    assert "TRUSTFORGE_ONLINE_STANCE" not in env
    assert env["CACHE_BACKEND"] == "json"
    assert env["TRUSTFORGE_CACHE_JSON_PATH"].startswith("/tmp/")
    assert env["TRUSTFORGE_LIVE_TOKEN_SECRET_ARN"].startswith(
        "arn:aws:secretsmanager:us-east-1:850849012389:secret:competition/"
    )
    assert env["TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID"]
    assert set(CONTRACT["secret_environment_inputs"]) == {
        "TRUSTFORGE_LIVE_TOKEN_SECRET_ARN",
        "TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID",
        "TRUSTFORGE_ARKHAM_SECRET_ARN",
        "TRUSTFORGE_ARKHAM_SECRET_VERSION_ID",
        "TRUSTFORGE_CMC_SECRET_ARN",
        "TRUSTFORGE_CMC_SECRET_VERSION_ID",
        "TRUSTFORGE_ETHERSCAN_SECRET_ARN",
        "TRUSTFORGE_ETHERSCAN_SECRET_VERSION_ID",
        "TRUSTFORGE_WHALE_ALERT_SECRET_ARN",
        "TRUSTFORGE_WHALE_ALERT_SECRET_VERSION_ID",
    }
    provider_arns = [
        value for key, value in env.items()
        if key.endswith("_SECRET_ARN") and key != "TRUSTFORGE_LIVE_TOKEN_SECRET_ARN"
    ]
    assert len(provider_arns) == 4
    assert all("/providers/" in arn and "*" not in arn for arn in provider_arns)


def test_live_routes_and_iam_are_allowlists_without_wildcards():
    assert CONTRACT["public_requests"] == [
        {"method": "GET", "path": "/"},
        {"method": "GET", "path": "/healthz"},
    ]
    assert {entry["path"] for entry in CONTRACT["token_protected_requests"]} == {
        "/analyze",
        "/analyze.json",
    }
    assert CONTRACT["bedrock"]["actions"] == ["bedrock:InvokeModel"]
    assert all("*" not in arn for arn in CONTRACT["bedrock"]["resources"])
    assert CONTRACT["dynamodb"]["budget_table"]["actions"] == [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    ]
    assert CONTRACT["dynamodb"]["cost_ledger_table"]["actions"] == [
        "dynamodb:PutItem",
        "dynamodb:Scan",
    ]


def test_execution_role_and_function_url_policies_are_exact():
    role = CONTRACT["execution_role"]
    assert role["trust_policy"] == {
        "principal": "lambda.amazonaws.com",
        "action": "sts:AssumeRole",
    }
    allowed_actions = {
        action for statement in role["statements"] for action in statement["actions"]
    }
    assert allowed_actions == {
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "secretsmanager:GetSecretValue",
        "bedrock:InvokeModel",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:PutItem",
        "dynamodb:Scan",
    }
    for statement in role["statements"]:
        assert statement["resources"]
        assert all(resource != "*" for resource in statement["resources"])
        assert all("850849012389" in resource or "::foundation-model/" in resource
                   for resource in statement["resources"])
    assert CONTRACT["deployment_alias"] == "live"
    assert {entry["statement_id"] for entry in CONTRACT["function_url_policy"]} == {
        "FunctionURLAllowPublicAccess", "FunctionURLAllowPublicInvoke"
    }
    assert {entry["principal"] for entry in CONTRACT["function_url_policy"]} == {"*"}
    assert {entry["resource"] for entry in CONTRACT["function_url_policy"]} == {
        "arn:aws:lambda:us-east-1:850849012389:function:competition-trustforge-team11-live:live"
    }
