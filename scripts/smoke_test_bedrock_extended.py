#!/usr/bin/env python3
"""Bedrock 擴展 Smoke Test — 驗證敘事模型與 stance 模型皆可用（issue #863）。

用法：
  python scripts/smoke_test_bedrock_extended.py

驗證項目：
  1. BedrockClient.complete() — 主敘事模型（BEDROCK_MODEL_ID）
  2. BedrockClient.classify_stance() — stance 分類模型（BEDROCK_HAIKU_MODEL_ID）

輸出：
  out/bedrock_smoke_test.json — 結構化結果（模型/區域/tokens/耗時/成功狀態）

安全：不記錄任何 credential/token/secret。

與既有 scripts/bedrock_smoke_test.py 的差異：
  - 既有版只驗 complete()（敘事模型），委託 src/trustforge/smoke.py
  - 本版額外驗證 classify_stance()（stance 模型），且用 BedrockClient 高階介面
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


def _classify_error_type(exc: Exception) -> str:
    """分類錯誤類型，供人類快速定位問題。"""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "credential" in msg or "token" in msg or "security" in msg:
        return "credential"
    if "access" in msg or "permission" in msg or "authoriz" in msg:
        return "permission"
    if "not found" in msg or "does not exist" in msg or "validationexception" in msg:
        return "model-not-found"
    if "timeout" in msg or "timed out" in msg or "connect" in msg:
        return "timeout"
    return f"unknown ({name})"


def run_extended_smoke() -> int:
    """Execute extended Bedrock smoke test. Returns 0=pass, 1=fail."""
    from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402

    results: dict = {
        "test": "bedrock-extended-smoke",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "region": os.getenv("AWS_REGION", ""),
        "model_id": os.getenv("BEDROCK_MODEL_ID", ""),
        "stance_model_id": os.getenv("BEDROCK_HAIKU_MODEL_ID", ""),
        "tests": [],
        "overall": "pending",
    }

    # Pre-check: 環境變數
    if not results["model_id"]:
        results["overall"] = "fail"
        results["error"] = "BEDROCK_MODEL_ID 未設定"
        _write_and_report(results)
        return 1
    if not results["region"]:
        results["overall"] = "fail"
        results["error"] = "AWS_REGION 未設定"
        _write_and_report(results)
        return 1

    # 建立線上 client
    try:
        client = BedrockClient(offline=False)
    except Exception as exc:
        results["overall"] = "fail"
        results["error"] = f"BedrockClient 建立失敗：{type(exc).__name__}: {exc}"
        _write_and_report(results)
        return 1

    all_pass = True

    # --- Test 1: complete() —————————————————————————————————————————————
    test_complete: dict = {"name": "complete", "status": "pending"}
    t0 = time.time()
    try:
        result = client.complete(
            system="你是一個測試助手。只回覆 OK。",
            prompt="請回覆 OK",
        )
        elapsed = time.time() - t0
        test_complete.update({
            "status": "success",
            "elapsed_sec": round(elapsed, 3),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "response_length": len(result.text),
            "response_preview": result.text[:100],
            "model_id": result.model_id,
        })
    except Exception as exc:
        elapsed = time.time() - t0
        test_complete.update({
            "status": "fail",
            "elapsed_sec": round(elapsed, 3),
            "error_type": _classify_error_type(exc),
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        })
        all_pass = False
    results["tests"].append(test_complete)

    # --- Test 2: classify_stance() ———————————————————————————————————————
    test_stance: dict = {"name": "classify_stance", "status": "pending"}
    # 已知答案的測試對：語意蘊含
    a = "Bitcoin adoption continues to rise with institutional participation."
    b = "Institutional adoption of Bitcoin is steadily increasing."
    t0 = time.time()
    try:
        # 直接用 stance_offline=False 確保走真模型
        stance_client = BedrockClient(offline=False, stance_offline=False)
        label = stance_client.classify_stance(a, b)
        elapsed = time.time() - t0
        test_stance.update({
            "status": "success",
            "elapsed_sec": round(elapsed, 3),
            "result": label,
            "expected": "entailment",
            "correct": label == "entailment",
            "stance_model_id": stance_client.config.stance_model_id,
        })
        # stance 呼叫後可能有 cost_events
        if stance_client.cost_events:
            ev = stance_client.cost_events[0]
            test_stance["input_tokens"] = ev.get("tokens_in", 0)
            test_stance["output_tokens"] = ev.get("tokens_out", 0)
            test_stance["cost_usd"] = ev.get("cost_usd", 0.0)
    except Exception as exc:
        elapsed = time.time() - t0
        test_stance.update({
            "status": "fail",
            "elapsed_sec": round(elapsed, 3),
            "error_type": _classify_error_type(exc),
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        })
        all_pass = False
    results["tests"].append(test_stance)

    # --- Test 3: classify_stance() contradiction case ————————————————————
    test_contra: dict = {"name": "classify_stance_contradiction", "status": "pending"}
    a2 = "Regulatory clarity will boost institutional adoption significantly."
    b2 = "Regulatory scrutiny will boost investor caution significantly."
    t0 = time.time()
    try:
        stance_client2 = BedrockClient(offline=False, stance_offline=False)
        label2 = stance_client2.classify_stance(a2, b2)
        elapsed = time.time() - t0
        test_contra.update({
            "status": "success",
            "elapsed_sec": round(elapsed, 3),
            "result": label2,
            "expected": "contradiction",
            "correct": label2 == "contradiction",
            "stance_model_id": stance_client2.config.stance_model_id,
        })
    except Exception as exc:
        elapsed = time.time() - t0
        test_contra.update({
            "status": "fail",
            "elapsed_sec": round(elapsed, 3),
            "error_type": _classify_error_type(exc),
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        })
        all_pass = False
    results["tests"].append(test_contra)

    # --- 彙總 ————————————————————————————————————————————————————————————
    results["overall"] = "pass" if all_pass else "fail"
    _write_and_report(results)
    return 0 if all_pass else 1


def _write_and_report(results: dict) -> None:
    """寫入 artifact JSON 並印出摘要。"""
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / "bedrock_smoke_test.json"
    artifact_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"📄 Artifact: {artifact_path}")

    overall = results["overall"]
    if overall == "pass":
        print(f"✅ Extended smoke test 通過")
        for t in results.get("tests", []):
            status_icon = "✓" if t["status"] == "success" else "✗"
            print(f"  {status_icon} {t['name']}: {t['status']} ({t.get('elapsed_sec', '?')}s)")
    else:
        print(f"❌ Extended smoke test 失敗: {results.get('error', '')}", file=sys.stderr)
        for t in results.get("tests", []):
            if t["status"] == "fail":
                print(f"  ✗ {t['name']}: {t.get('error_type', '')} — {t.get('error', '')}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(run_extended_smoke())
