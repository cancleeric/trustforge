#!/usr/bin/env python3
"""Issue #84：離線量測 5 幣 demo 動線的 stance cache hit rate。

⛔ 全程不打真 AWS：重用 `gen_stance_cache.py` 的離線候選對枚舉邏輯
（`collect(coin, coin=coin, offline=True)` + `BedrockClient(offline=True)
.extract_claims_with_llm()` 走 regex fallback + `_corroboration_detail()`
過濾閘），只對既有持久化 `stance_cache.json` 做**唯讀查詢**判斷命中與否，
完全不呼叫 `classify_stance`/`classify_stance_strict`，不需要 AWS 憑證。

用法：
    python3 scripts/measure_stance_cache_hit_rate.py
    python3 scripts/measure_stance_cache_hit_rate.py --cache <path> --threshold 0.8
    python3 scripts/measure_stance_cache_hit_rate.py --coins BTC ETH

驗收（issue #84）：5 幣 demo 動線 stance cache hit rate ≥80%。exit code：
達標 0，未達標 1（供 CI／CEO 驗收 gate 判斷）。

⚠️ 已知侷限：本量測用的候選對來自「離線 sample_data」claims 枚舉（跟
`gen_stance_cache.py` 建立持久化快取用的同一份離線資料/邏輯），並非公開
`/analyze?real=1`（`data_mode=live`）真連接器當下抓到的即時資料。真實 demo
若走真連接器，候選 claims 內容可能不同——這是離線環境（無 AWS 憑證/無法真
執行 `/analyze`）下可達到的最佳量測代理，跟 `scripts/gen_stance_cache.py`
本身产生持久化快取所用的候選對枚舉方式完全一致（同一份過濾邏輯，不會
drift），真實線上 hit rate 仍需由能存取真連接器的環境另行驗證。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

_GEN_SCRIPT_PATH = _REPO / "scripts" / "gen_stance_cache.py"
_spec = importlib.util.spec_from_file_location("gen_stance_cache", _GEN_SCRIPT_PATH)
_gen = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gen_stance_cache", _gen)
_spec.loader.exec_module(_gen)

from trustforge.trust.stance_cache import DEFAULT_CACHE_PATH, StanceCache  # noqa: E402

DEFAULT_THRESHOLD = 0.8


def measure(cache_path: str | Path = DEFAULT_CACHE_PATH, coins: list[str] | None = None) -> dict:
    """離線量測 `coins`（預設 5 幣 `gen_stance_cache.COINS`）demo 動線的 stance
    cache hit rate。

    做法：對每個幣別各自離線收集 claims、枚舉候選對（跟 `gen_stance_cache.py`
    產生持久化快取用的同一套邏輯），再用唯讀的 `StanceCache(cache_path)` 查詢
    每一對是否命中（version 相符的合法 label）。彙總（跨幣依 `cache_key`
    去重，跟 `enumerate_candidate_pairs()` 合併規則一致）算總 hit rate；同時
    保留逐幣拆分供除錯（`per_coin`：某一幣單獨 hit rate 偏低時方便定位）。

    回傳 `{"total": int, "hits": int, "hit_rate": float, "per_coin": {coin:
    {"total": int, "hits": int}}}`。`total == 0`（無任何候選對，如離線樣本
    資料完全沒有可比對的重疊 claims）時 `hit_rate` 定義為 `1.0`（沒有任何 pair
    需要打真 Bedrock，天然 100% 免外呼，不該被誤判成「未達標」）。
    """
    coins = coins if coins is not None else list(_gen.COINS)
    cache = StanceCache(Path(cache_path))
    per_coin: dict[str, dict[str, int]] = {}
    merged_pairs: dict[str, tuple[str, str]] = {}
    for coin in coins:
        claims = _gen.collect_claims_for_coin(coin)
        coin_pairs = _gen.enumerate_candidate_pairs_for_claims(claims)
        coin_hits = sum(1 for a, b in coin_pairs.values() if cache.get(a, b) is not None)
        per_coin[coin] = {"total": len(coin_pairs), "hits": coin_hits}
        for key, pair in coin_pairs.items():
            merged_pairs.setdefault(key, pair)

    total = len(merged_pairs)
    hits = sum(1 for a, b in merged_pairs.values() if cache.get(a, b) is not None)
    hit_rate = (hits / total) if total else 1.0
    return {"total": total, "hits": hits, "hit_rate": hit_rate, "per_coin": per_coin}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--cache", default=str(DEFAULT_CACHE_PATH),
        help=f"讀取的 stance_cache.json 路徑（預設 {DEFAULT_CACHE_PATH}）",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"達標門檻（0~1，預設 {DEFAULT_THRESHOLD}）",
    )
    parser.add_argument(
        "--coins", nargs="+", default=None,
        help="要量測的幣別清單（預設 5 幣 demo 動線：BTC ETH SOL BNB XRP）",
    )
    args = parser.parse_args(argv)

    result = measure(args.cache, args.coins)
    for coin, stat in result["per_coin"].items():
        pct = (stat["hits"] / stat["total"] * 100) if stat["total"] else 100.0
        print(f"{coin}: {stat['hits']}/{stat['total']} ({pct:.1f}%)")
    print(
        f"總計（跨幣去重）：{result['hits']}/{result['total']} "
        f"({result['hit_rate'] * 100:.1f}%)"
    )

    if result["hit_rate"] >= args.threshold:
        print(f"達標（門檻 {args.threshold * 100:.0f}%）")
        return 0
    print(f"未達標（門檻 {args.threshold * 100:.0f}%）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
