"""#1347 production artifacts must share one immutable Bedrock gate identity."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "TRUSTFORGE_BEDROCK_RPS_BACKEND": "dynamodb",
    "TRUSTFORGE_BEDROCK_RPS_REGION": "us-east-1",
    "TRUSTFORGE_BEDROCK_RPS_TABLE": "competition-trustforge-team11-budget",
}


def _default(script: str, key: str) -> str:
    match = re.search(rf'\$\{{{key}:-([^}}]+)\}}', script)
    assert match is not None, key
    return match.group(1)


def test_lambda_ec2_activation_and_workers_share_exact_gate_identity() -> None:
    contract = json.loads(
        (ROOT / "deploy/competition-lambda-live-contract.json").read_text()
    )["environment"]
    ec2 = (ROOT / "deploy/deploy_ec2.sh").read_text()
    activation = (ROOT / "deploy/activate_release.sh").read_text()
    scheduler = (ROOT / "deploy/install_hermes_scheduler.sh").read_text()

    for key, value in EXPECTED.items():
        assert contract[key] == value
        assert _default(ec2, key) == value
        assert _default(activation, key) == value
        assert _default(scheduler, key) == value
        # Both Hermes and analysis-flow units receive the exact identity.
        assert scheduler.count(f"Environment={key}=${{{key.removeprefix('TRUSTFORGE_')}}}") == 0
        variable = {
            "TRUSTFORGE_BEDROCK_RPS_BACKEND": "BEDROCK_RPS_BACKEND",
            "TRUSTFORGE_BEDROCK_RPS_REGION": "BEDROCK_RPS_REGION",
            "TRUSTFORGE_BEDROCK_RPS_TABLE": "BEDROCK_RPS_TABLE",
        }[key]
        assert scheduler.count(f"Environment={key}=${variable}") == 2

    assert "reconcile_bedrock_rps_service_env.sh" in activation
    assert "install_hermes_scheduler.sh" in activation
    assert ".activation-bedrock-rps-units.bak" in activation
    assert "hermes-cycle.service trustforge-analysis-flow.service" in activation


def test_ec2_gate_iam_is_exact_and_never_creates_a_table() -> None:
    script = ROOT / "deploy/setup_bedrock_rps_iam.sh"
    result = subprocess.run(
        ["bash", str(script), "--print-policy"],
        env={**os.environ, **EXPECTED},
        text=True,
        capture_output=True,
        check=True,
    )
    policy = json.loads(result.stdout)
    statement = policy["Statement"][0]
    assert set(statement["Action"]) == {"dynamodb:GetItem", "dynamodb:UpdateItem"}
    assert statement["Resource"].endswith(
        ":table/competition-trustforge-team11-budget"
    )
    source = script.read_text()
    assert "create-table" not in source
    assert "update-table" not in source


def test_reconcile_script_sets_all_three_primary_service_values(tmp_path: Path) -> None:
    service = tmp_path / "trustforge.service"
    service.write_text("[Service]\nEnvironment=PYTHONPATH=/opt/trustforge\n")
    env = {
        **os.environ,
        **EXPECTED,
        "TRUSTFORGE_SERVICE_FILE": str(service),
    }
    subprocess.run(
        ["bash", str(ROOT / "deploy/reconcile_bedrock_rps_service_env.sh")],
        env=env,
        check=True,
    )
    rendered = service.read_text()
    for key, value in EXPECTED.items():
        assert rendered.count(f"Environment={key}={value}") == 1

    # Idempotent reconciliation must replace, not duplicate.
    subprocess.run(
        ["bash", str(ROOT / "deploy/reconcile_bedrock_rps_service_env.sh")],
        env=env,
        check=True,
    )
    rendered = service.read_text()
    for key, value in EXPECTED.items():
        assert rendered.count(f"Environment={key}={value}") == 1
