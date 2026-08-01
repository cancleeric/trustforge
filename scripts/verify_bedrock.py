#!/usr/bin/env python3
"""Bedrock 環境與 IAM 權限驗證（issue #863）。

用法：
  python scripts/verify_bedrock.py
  python scripts/verify_bedrock.py --dry-run   # 只檢查環境變數，不呼叫 AWS

驗證項目：
  1. BEDROCK_MODEL_ID、BEDROCK_HAIKU_MODEL_ID、AWS_REGION 環境變數已設定
  2. AWS credentials 有效（STS get-caller-identity）
  3. bedrock-runtime client 可建立
  4. 模型存取權有效（list-foundation-models 或等效低成本驗證）

安全：不在任何輸出中揭露 credential/token/secret/完整 ARN。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))


def _check_env_vars() -> dict:
    """檢查必要環境變數。"""
    checks: dict = {}
    required = {
        "AWS_REGION": os.getenv("AWS_REGION", "").strip(),
        "BEDROCK_MODEL_ID": os.getenv("BEDROCK_MODEL_ID", "").strip(),
    }
    optional = {
        "BEDROCK_HAIKU_MODEL_ID": os.getenv("BEDROCK_HAIKU_MODEL_ID", "").strip(),
    }

    all_ok = True
    for name, value in required.items():
        if value:
            checks[name] = {"status": "set", "value": value}
        else:
            checks[name] = {"status": "missing", "value": None}
            all_ok = False

    for name, value in optional.items():
        if value:
            checks[name] = {"status": "set", "value": value}
        else:
            checks[name] = {"status": "not_set (optional)", "value": None}

    checks["_all_required_set"] = all_ok
    return checks


def _check_credentials() -> dict:
    """驗證 AWS credentials 有效（STS get-caller-identity）。

    只記錄 UserId 前綴（不揭露完整 ARN 或帳號 ID）。
    """
    try:
        import boto3  # noqa: PLC0415

        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        # 安全：只記錄 UserId 前 8 字元作為有效性證明，不揭露完整 ARN
        user_id = identity.get("UserId", "")
        return {
            "status": "valid",
            "user_id_prefix": user_id[:8] + "..." if len(user_id) > 8 else user_id,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:200],
        }


def _check_bedrock_access(region: str) -> dict:
    """嘗試建立 bedrock-runtime client 並驗證基本存取。"""
    try:
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        # 嘗試建立 runtime client
        from trustforge.bedrock import create_bedrock_runtime_client

        runtime_client = create_bedrock_runtime_client(
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 1},
            ),
        )
        # 驗證 client 可用（不實際呼叫模型）
        # 用 bedrock（非 runtime）的 list_foundation_models 做低成本驗證
        bedrock_client = boto3.client("bedrock", region_name=region)
        resp = bedrock_client.list_foundation_models(byOutputModality="TEXT")
        model_count = len(resp.get("modelSummaries", []))
        return {
            "status": "accessible",
            "runtime_client": "ok",
            "available_text_models": model_count,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:200],
        }


def run_verify(dry_run: bool = False) -> int:
    """Execute environment verification. Returns 0 on success, 1 on failure."""
    results: dict = {
        "test": "bedrock-environment-verify",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "checks": {},
        "overall": "pending",
    }

    # Step 1: 環境變數
    env_checks = _check_env_vars()
    results["checks"]["environment_variables"] = env_checks

    if not env_checks["_all_required_set"]:
        results["overall"] = "fail"
        _print_result(results)
        return 1

    if dry_run:
        results["overall"] = "pass (dry-run, skipped AWS calls)"
        _print_result(results)
        return 0

    # Step 2: Credentials
    cred_check = _check_credentials()
    results["checks"]["credentials"] = cred_check
    if cred_check["status"] != "valid":
        results["overall"] = "fail"
        _print_result(results)
        return 1

    # Step 3: Bedrock 存取
    region = os.getenv("AWS_REGION", "").strip()
    access_check = _check_bedrock_access(region)
    results["checks"]["bedrock_access"] = access_check
    if access_check["status"] != "accessible":
        results["overall"] = "fail"
        _print_result(results)
        return 1

    results["overall"] = "pass"
    _print_result(results)
    return 0


def _print_result(results: dict) -> None:
    """輸出結構化 JSON 結果。"""
    print(json.dumps(results, ensure_ascii=False, indent=2))
    overall = results["overall"]
    if "pass" in overall:
        print(f"\n✅ 環境驗證通過：{overall}", file=sys.stderr)
    else:
        print(f"\n❌ 環境驗證失敗：{overall}", file=sys.stderr)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sys.exit(run_verify(dry_run=dry_run))
