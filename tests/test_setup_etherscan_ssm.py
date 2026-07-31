from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "setup_etherscan_ssm.sh"


def test_etherscan_policy_is_least_privilege_exact_parameter():
    """Etherscan IAM policy 鎖在單一 exact SSM parameter ARN、6 個最小 ssm op，
    無任何 wildcard（resource/action 皆不可含 "*"）——防止 least-privilege 退化。"""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--print-policy"],
        check=True,
        capture_output=True,
        text=True,
    )
    policy = json.loads(result.stdout)
    statement = policy["Statement"][0]

    # (a) Action 集合：恰好 6 個 ssm op（不可多不可少 → 不會越權、也不會少到壞）。
    assert set(statement["Action"]) == {
        "ssm:GetParameter",
        "ssm:PutParameter",
        "ssm:DeleteParameter",
        "ssm:AddTagsToResource",
        "ssm:RemoveTagsFromResource",
        "ssm:ListTagsForResource",
    }

    # (d) Resource 是單一 exact ARN（str 而非 list，且指向唯一 etherscan parameter）。
    assert isinstance(statement["Resource"], str)
    assert statement["Resource"].endswith(
        ":parameter/trustforge/production/etherscan-api-key"
    )

    # (b) Resource 不可含 wildcard（exact parameter only）。
    assert "*" not in statement["Resource"]

    # (c) 每個 Action 皆不可含 wildcard（無 "ssm:*" / "ssm:Get*"）。
    actions = statement["Action"]
    if isinstance(actions, str):
        actions = [actions]
    assert "*" not in actions
    for action in actions:
        assert "*" not in action


def _run_with_parameter(parameter: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER": parameter}
    return subprocess.run(
        ["bash", str(SCRIPT), "--print-policy"],
        env=env,
        capture_output=True,
        text=True,
    )


def test_etherscan_rejects_trailing_dotdot_segment():
    """拒任何 `..` path segment（與 etherscan_secret._parameter_name() 一致）。
    舊版只拒 `/../` 子字串 → `/trustforge/production/..` 會過（IAM 佈建），但
    Python `_parameter_name()` 拒任何 `..` segment → runtime 判 unavailable、無聲
    失敗。本測鎖住結尾 `..` 被拒。"""
    result = _run_with_parameter("/trustforge/production/..")
    assert result.returncode != 0
    assert "invalid" in result.stderr.lower()


def test_etherscan_rejects_mid_dotdot_segment():
    """/foo/../bar 同樣含一個 `..` segment，必須被拒（不只是結尾）。"""
    result = _run_with_parameter("/foo/../bar")
    assert result.returncode != 0
    assert "invalid" in result.stderr.lower()


def test_etherscan_accepts_normal_parameter_without_dotdot():
    """正常參數名（無 `..` segment）仍通過——回歸保護，確認新檢查沒誤殺。"""
    result = _run_with_parameter("/trustforge/production/etherscan-api-key")
    assert result.returncode == 0
    # 仍能正常產出 policy（驗證沒在驗證邏輯裡誤 exit）。
    json.loads(result.stdout)
