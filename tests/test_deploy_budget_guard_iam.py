"""#75：budget guard 表 + 最小權限 IAM policy 斷言。

驗證 `deploy/setup_budget_guard_dynamodb.sh --print-policy` 印出的 policy JSON：
- Resource 鎖死 `trustforge-budget-guard` 表 ARN（可被 TRUSTFORGE_BUDGET_COUNTER_TABLE
  覆寫，但必須是某張表 ARN，不是 `*`）。
- Action 僅 `dynamodb:UpdateItem` / `dynamodb:GetItem`，**不含** `dynamodb:*`、
  PutItem / DeleteItem / Scan 等。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
_SCRIPT = _DEPLOY_DIR / "setup_budget_guard_dynamodb.sh"


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy 腳本不存在")
def test_budget_guard_iam_policy_is_minimal_and_table_scoped():
    out = subprocess.run(
        ["bash", str(_SCRIPT), "--print-policy"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    policy = json.loads(out.stdout.strip())
    assert policy["Version"] == "2012-10-17"
    stmts = policy["Statement"]
    assert len(stmts) == 1
    stmt = stmts[0]
    assert stmt["Effect"] == "Allow"

    actions = set(stmt["Action"])
    # 只允許這兩個 action（原子 conditional write + 狀態查詢所需）
    assert actions == {"dynamodb:UpdateItem", "dynamodb:GetItem"}
    # 絕不能出現萬用 / 寫入 / 刪除 / 掃描
    assert "dynamodb:*" not in actions
    assert "dynamodb:PutItem" not in actions
    assert "dynamodb:DeleteItem" not in actions
    assert "dynamodb:Scan" not in actions

    resource = stmt["Resource"]
    assert isinstance(resource, str)
    assert resource.startswith("arn:aws:dynamodb:"), resource
    assert ":table/" in resource, "Resource 必須鎖定到具體表 ARN，不能是 *"
    assert resource.endswith("table/trustforge-budget-guard"), resource


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy 腳本不存在")
def test_budget_guard_iam_policy_honors_table_override():
    out = subprocess.run(
        ["bash", str(_SCRIPT), "--print-policy"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "TRUSTFORGE_BUDGET_COUNTER_TABLE": "custom-bg-table"},
    )
    assert out.returncode == 0
    policy = json.loads(out.stdout.strip())
    assert policy["Statement"][0]["Resource"].endswith("table/custom-bg-table")
