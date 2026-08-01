"""Bedrock smoke test — 驗證 Bedrock 連線可用，產出 artifact 證明非離線（issue #202）。

用法：
  python -m trustforge.cli smoke
  python -m trustforge.cli smoke --out out/

驗證項目：
  1. BEDROCK_MODEL_ID 環境變數已設定
  2. AWS_REGION 已設定
  3. boto3 client 可建立（AWS 憑證可用）
  4. 一次簡單 prompt invoke 成功
  5. 回應不含 [OFFLINE] placeholder
  6. 輸出 artifact 到 out/bedrock-smoke-artifact.json

安全：不 hardcode 任何 credential，全走 env / boto3 default chain。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run_smoke(out_dir: str = "out") -> int:
    """Execute Bedrock smoke test. Returns 0 on success, 1 on failure."""
    results: dict = {
        "test": "bedrock-smoke",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "success": False,
    }

    # ── Check 1: BEDROCK_MODEL_ID 已設定 ──────────────────────────────────
    model_id = os.getenv("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        results["checks"]["model_id"] = {
            "passed": False,
            "error": "BEDROCK_MODEL_ID 環境變數未設定。請設定後重試。",
        }
        _write_artifact(results, out_dir)
        print("❌ BEDROCK_MODEL_ID 未設定", file=sys.stderr)
        return 1
    results["checks"]["model_id"] = {"passed": True, "value": model_id}

    # ── Check 2: AWS_REGION 已設定 ────────────────────────────────────────
    region = os.getenv("AWS_REGION", "").strip()
    if not region:
        results["checks"]["region"] = {
            "passed": False,
            "error": "AWS_REGION 環境變數未設定。請設定後重試。",
        }
        _write_artifact(results, out_dir)
        print("❌ AWS_REGION 未設定", file=sys.stderr)
        return 1
    results["checks"]["region"] = {"passed": True, "value": region}

    # ── Check 3: boto3 client 可建立 ─────────────────────────────────────
    try:
        from botocore.config import Config  # noqa: PLC0415
        from .bedrock import create_bedrock_runtime_client

        client = create_bedrock_runtime_client(
            region_name=region,
            config=Config(
                read_timeout=30,
                connect_timeout=10,
                retries={"total_max_attempts": 1},
            ),
        )
        results["checks"]["boto3_client"] = {"passed": True}
    except Exception as exc:
        results["checks"]["boto3_client"] = {
            "passed": False,
            "error": f"無法建立 boto3 bedrock-runtime client：{type(exc).__name__}: {exc}",
        }
        _write_artifact(results, out_dir)
        print(f"❌ boto3 client 建立失敗：{exc}", file=sys.stderr)
        return 1

    # ── Check 4: Invoke 一次簡單 prompt ───────────────────────────────────
    prompt = "Say exactly: BEDROCK_SMOKE_OK"
    start = time.time()
    try:
        from .bedrock import bedrock_invoke_slot

        with bedrock_invoke_slot():
            response = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 64, "temperature": 0.0},
            )
        elapsed = time.time() - start

        # Extract response text
        output_message = response.get("output", {}).get("message", {})
        content_blocks = output_message.get("content", [])
        response_text = ""
        for block in content_blocks:
            if "text" in block:
                response_text += block["text"]

        # Token usage
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        results["checks"]["invoke"] = {
            "passed": True,
            "response_text": response_text[:200],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "elapsed_sec": round(elapsed, 2),
            "model_id": model_id,
        }
    except Exception as exc:
        elapsed = time.time() - start
        results["checks"]["invoke"] = {
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(elapsed, 2),
        }
        _write_artifact(results, out_dir)
        print(f"❌ Bedrock invoke 失敗：{exc}", file=sys.stderr)
        return 1

    # ── Check 5: 回應不含 [OFFLINE] placeholder ──────────────────────────
    has_offline = "[OFFLINE]" in response_text
    results["checks"]["no_offline_placeholder"] = {
        "passed": not has_offline,
        "note": "回應不含 [OFFLINE]" if not has_offline else "回應包含 [OFFLINE] placeholder！",
    }
    if has_offline:
        _write_artifact(results, out_dir)
        print("❌ 回應包含 [OFFLINE] placeholder", file=sys.stderr)
        return 1

    # ── 全部通過 ─────────────────────────────────────────────────────────
    results["success"] = True
    results["summary"] = (
        f"Bedrock smoke 通過：model={model_id}, region={region}, "
        f"tokens={input_tokens}+{output_tokens}, elapsed={elapsed:.2f}s"
    )
    _write_artifact(results, out_dir)
    print(f"✅ {results['summary']}")
    return 0


def _write_artifact(results: dict, out_dir: str) -> None:
    """Write smoke test artifact to out_dir."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifact_path = out / "bedrock-smoke-artifact.json"
    artifact_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"📄 Artifact 寫入：{artifact_path}")
