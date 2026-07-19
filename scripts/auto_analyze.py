#!/usr/bin/env python3
"""TrustForge 自動分析 — 事件驅動：有新資料才分析。

流程：
1. 檢查各幣種的 cache 是否有更新（比對 mtime）
2. 有新資料 → 觸發分析 → 寫 feature_store → 觸發升級
3. 沒新資料 → 跳過，等下一輪檢查
4. 檢查間隔 60 秒
"""
import json
import os
import time
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost_tracker import record_call, get_summary
from auto_upgrade import run_upgrade_cycle

import requests

AGENTCORE_URL = "http://127.0.0.1:8080/invocations"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
CHECK_INTERVAL_SEC = 60  # 每 60 秒檢查一次有沒有新資料
RESULTS_LOG = Path(__file__).resolve().parent / "logs" / "analysis_results.jsonl"

# 資料 cache 目錄（TrustForge 的 ingestion cache）
CACHE_DIR = Path(__file__).resolve().parents[1] / "out" / "cache"
# 如果沒有 cache 目錄，用 data/sample 的 mtime 做 fallback
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"

# 記錄每個幣種上次分析時的資料時間戳
_last_data_mtime: dict[str, float] = {}


def get_data_mtime(coin: str) -> float:
    """取得某幣種最新資料的 mtime。"""
    # 優先看 cache（live 爬蟲產出）
    coin_cache = CACHE_DIR / coin.lower()
    if coin_cache.exists():
        files = list(coin_cache.glob("*"))
        if files:
            return max(f.stat().st_mtime for f in files)

    # fallback: sample data
    coin_sample = SAMPLE_DIR / coin.lower()
    if coin_sample.exists():
        files = list(coin_sample.glob("*"))
        if files:
            return max(f.stat().st_mtime for f in files)

    # 都沒有就回 0（強制跑一次）
    return 0.0


def has_new_data(coin: str) -> bool:
    """檢查某幣種是否有新資料。"""
    current_mtime = get_data_mtime(coin)
    last_mtime = _last_data_mtime.get(coin, 0.0)
    return current_mtime > last_mtime


def analyze(coin: str) -> dict:
    prompt = f"分析 {coin} 目前市場情緒，使用 live 真實資料模式，標注信任分數和關鍵事實"
    start = time.time()
    try:
        resp = requests.post(AGENTCORE_URL, json={"prompt": prompt}, timeout=120)
        duration = time.time() - start
        text = ""
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                chunk = line[6:]
                try:
                    text += json.loads(chunk)
                except (json.JSONDecodeError, TypeError):
                    text += chunk

        # 記錄成本
        cost = record_call(coin, prompt, text, duration)

        # 存分析結果
        result_record = {
            "timestamp": datetime.now().isoformat(),
            "coin": coin,
            "response": text,
            "duration_sec": round(duration, 2),
            "tokens": cost["total_tokens"],
            "cost_usd": cost["total_cost_usd"],
        }
        RESULTS_LOG.parent.mkdir(exist_ok=True)
        with open(RESULTS_LOG, "a") as f:
            f.write(json.dumps(result_record, ensure_ascii=False) + "\n")

        # 更新 mtime 記錄
        _last_data_mtime[coin] = get_data_mtime(coin)

        return {"coin": coin, "status": "ok", "response": text[:200], "cost": cost}
    except Exception as e:
        return {"coin": coin, "status": "error", "error": str(e)}


def main():
    check_num = 0
    print(f"[{datetime.now().isoformat()}] TrustForge 事件驅動分析啟動")
    print(f"  幣種: {COINS}")
    print(f"  檢查間隔: {CHECK_INTERVAL_SEC}s")
    print(f"  模式: 有新資料才分析")
    print(f"  AgentCore: {AGENTCORE_URL}")
    print("=" * 60)

    # 第一輪強制跑一次（初始化）
    print(f"\n[初始化] 首次全幣種分析...")
    analyzed = []
    for coin in COINS:
        print(f"  分析 {coin}...", end=" ", flush=True)
        result = analyze(coin)
        if result["status"] == "ok":
            c = result["cost"]
            print(f"✅ tokens:{c['total_tokens']} cost:${c['total_cost_usd']:.4f} ({c['duration_sec']:.1f}s)")
            analyzed.append(coin)
        else:
            print(f"❌ {result['error']}")

    if analyzed:
        print(f"\n  🔄 觸發外框模組升級...")
        try:
            upgrade_result = run_upgrade_cycle()
            fs = upgrade_result["modules"].get("feature_store", {})
            print(f"  ✅ 升級完成 | features: {fs.get('total_features', 0)}")
        except Exception as e:
            print(f"  ⚠️ 升級失敗: {e}")

    summary = get_summary()
    print(f"\n  📊 累計: {summary['total_calls']} calls | ${summary['total_cost_usd']:.4f}")

    # 持續監控新資料
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        check_num += 1

        coins_with_new_data = [coin for coin in COINS if has_new_data(coin)]

        if not coins_with_new_data:
            if check_num % 10 == 0:  # 每 10 次檢查才印一次（避免刷屏）
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] 無新資料，等待中...")
            continue

        print(f"\n[{datetime.now().isoformat()}] 偵測到新資料: {coins_with_new_data}")
        analyzed = []
        for coin in coins_with_new_data:
            print(f"  分析 {coin}...", end=" ", flush=True)
            result = analyze(coin)
            if result["status"] == "ok":
                c = result["cost"]
                print(f"✅ tokens:{c['total_tokens']} cost:${c['total_cost_usd']:.4f}")
                analyzed.append(coin)
            else:
                print(f"❌ {result['error']}")

        if analyzed:
            print(f"  🔄 觸發外框模組升級...")
            try:
                upgrade_result = run_upgrade_cycle()
                fs = upgrade_result["modules"].get("feature_store", {})
                imp = upgrade_result["modules"].get("improvement", {})
                print(f"  ✅ 升級完成 | features: {fs.get('total_features', 0)} | proposals: {imp.get('proposals_count', 0)}")
            except Exception as e:
                print(f"  ⚠️ 升級失敗: {e}")

        summary = get_summary()
        print(f"  📊 累計: {summary['total_calls']} calls | ${summary['total_cost_usd']:.4f}")


if __name__ == "__main__":
    main()
