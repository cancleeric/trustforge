from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "deploy" / "setup_whale_alert_ssm.sh"
)


def test_policy_is_limited_to_exact_whale_alert_parameter():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--print-policy"],
        check=True,
        capture_output=True,
        text=True,
    )
    policy = json.loads(result.stdout)
    statement = policy["Statement"][0]

    assert statement["Resource"].endswith(
        ":parameter/trustforge/production/whale-alert-api-key"
    )
    assert set(statement["Action"]) == {
        "ssm:GetParameter",
        "ssm:PutParameter",
        "ssm:DeleteParameter",
        "ssm:AddTagsToResource",
        "ssm:ListTagsForResource",
    }
    assert "*" not in statement["Resource"]
