#!/usr/bin/env python3
"""TrustForge AgentCore 成本記錄器。

記錄每次 AgentCore 呼叫的 token 用量和費用。
寫入 JSON Lines 格式到 logs/agentcore_costs.jsonl。
"""
import json
import os
from datetime import datetime
from pathlib import Path

# Claude Sonnet 4.5 on Bedrock pricing
PRICE_INPUT_PER_TOKEN = 3.0 / 1_000_000   # $3 per 1M input tokens
PRICE_OUTPUT_PER_TOKEN = 15.0 / 1_000_000  # $15 per 1M output tokens

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
COST_LOG = LOG_DIR / "agentcore_costs.jsonl"
SUMMARY_FILE = LOG_DIR / "cost_summary.json"


def estimate_tokens(text: str) -> int:
    """粗估 token 數（中文約 1.5 chars/token，英文約 4 chars/token）。"""
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_chars = len(text) - cn_chars
    return int(cn_chars / 1.5 + en_chars / 4)


def record_call(coin: str, prompt: str, response: str, duration_sec: float):
    """記錄一次 AgentCore 呼叫的成本。"""
    input_tokens = estimate_tokens(prompt) + 800  # system prompt ~800 tokens
    output_tokens = estimate_tokens(response)
    input_cost = input_tokens * PRICE_INPUT_PER_TOKEN
    output_cost = output_tokens * PRICE_OUTPUT_PER_TOKEN
    total_cost = input_cost + output_cost

    record = {
        "timestamp": datetime.now().isoformat(),
        "coin": coin,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "duration_sec": round(duration_sec, 2),
        "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    }

    with open(COST_LOG, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    _update_summary(record)
    return record


def _update_summary(record: dict):
    """更新累計成本摘要。"""
    if SUMMARY_FILE.exists():
        with open(SUMMARY_FILE) as f:
            summary = json.load(f)
    else:
        summary = {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0, "by_coin": {}, "started_at": datetime.now().isoformat()}

    summary["total_calls"] += 1
    summary["total_tokens"] += record["total_tokens"]
    summary["total_cost_usd"] = round(summary["total_cost_usd"] + record["total_cost_usd"], 6)
    summary["last_updated"] = datetime.now().isoformat()

    coin = record["coin"]
    if coin not in summary["by_coin"]:
        summary["by_coin"][coin] = {"calls": 0, "tokens": 0, "cost_usd": 0}
    summary["by_coin"][coin]["calls"] += 1
    summary["by_coin"][coin]["tokens"] += record["total_tokens"]
    summary["by_coin"][coin]["cost_usd"] = round(summary["by_coin"][coin]["cost_usd"] + record["total_cost_usd"], 6)

    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def get_summary() -> dict:
    """取得成本摘要。"""
    if SUMMARY_FILE.exists():
        with open(SUMMARY_FILE) as f:
            return json.load(f)
    return {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0}


if __name__ == "__main__":
    print(json.dumps(get_summary(), indent=2, ensure_ascii=False))
