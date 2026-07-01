#!/usr/bin/env python3
"""Issue #12：離線枚舉 stance 候選對 + （CEO 親手執行）呼叫真 Bedrock 產生
`demo/sample_data/stance_cache.json`。

用法:
    python3 scripts/gen_stance_cache.py --dry-run          # 只列候選對，不呼叫、不寫檔
    python3 scripts/gen_stance_cache.py                    # 真呼叫 Bedrock 並寫回 --out
    python3 scripts/gen_stance_cache.py --out <path>        # 自訂輸出路徑（預設
                                                             # demo/sample_data/stance_cache.json）

流程：
1. 離線用 `collect(coin, coin=coin, offline=True)` + `BedrockClient(offline=True)
   .extract_claims_with_llm(docs)` 取得 BTC/ETH/SOL/BNB/XRP 五幣的 claims（純本地，
   不打真 AWS——offline client 內部走 regex fallback，見 bedrock.py）。
2. 對每一幣的 claims，重用 `trust.scoring._corroboration_detail()` 的過濾邏輯
   （overlap>=0.4 前置閘 + 方向閘 + 同來源排除）枚舉「會被送進 stance_fn 判斷」的
   候選對；用一個只記錄、永遠回 "neutral" 的假 stance_fn 餵給它（不影響枚舉結果，
   只是借用同一份判斷順序/排除邏輯，不新增任何真呼叫）。
3. 用 `stance_cache.cache_key()` 對候選對去重（(a,b) 與 (b,a) 視為同一對），跨 5 幣
   合併成唯一候選對清單。
4. `--dry-run`：只印出候選對，不呼叫 client、不寫檔。
   否則：對每一對呼叫 `client.classify_stance(a, b)`（真 Bedrock，非 offline
   client），依 `cache_key(a, b)` 存成 `{"label": label, "version":
   STANCE_CACHE_VERSION}`，跟既有快取檔 merge（舊 key 保留，新 key 覆蓋/新增）後
   整份覆寫回 `--out`。

⚠️ 本檔本身不含任何呼叫入口保護以外的巧門——真正打 AWS 只發生在非 --dry-run 且
傳入非 offline 的 `BedrockClient` 時。CEO 親手執行前務必確認環境變數
（`BEDROCK_HAIKU_MODEL_ID` / `AWS_REGION` / AWS 憑證）已就緒。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.bedrock import BedrockClient  # noqa: E402
from trustforge.ingestion.base import collect  # noqa: E402
from trustforge.trust.scoring import Claim, _corroboration_detail  # noqa: E402
from trustforge.trust.stance_cache import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    STANCE_CACHE_VERSION,
    cache_key,
)

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


def collect_claims_for_coin(coin: str) -> list[Claim]:
    """離線收集單一幣別的 claims：`collect()` 取樣本 docs，
    `BedrockClient(offline=True).extract_claims_with_llm()` 抽 claim（offline
    模式內部走 regex fallback，不打真 AWS）。
    """
    docs = collect(coin, coin=coin, offline=True)
    client = BedrockClient(offline=True)
    return client.extract_claims_with_llm(docs)


def enumerate_candidate_pairs_for_claims(claims: list[Claim]) -> dict[str, tuple[str, str]]:
    """重用 `_corroboration_detail()` 的過濾邏輯（overlap>=0.4 前置閘 + 方向閘 +
    同來源排除），枚舉「會被送進 stance_fn 判斷」的候選對。

    用一個只記錄候選對、永遠回 "neutral" 的假 stance_fn 餵給 `_corroboration_detail`，
    藉此完全不重寫過濾邏輯（避免 drift），也不產生任何真呼叫。回傳
    `{cache_key(a, b): (a, b)}`，key 已依 `cache_key` 去重（(a,b)/(b,a) 同對）。
    """
    found: dict[str, tuple[str, str]] = {}

    def _recorder(a: str, b: str) -> str:
        key = cache_key(a, b)
        if key not in found:
            found[key] = (a, b)
        return "neutral"  # 假設非矛盾，讓迴圈行為與「全部 neutral」情境一致地繼續

    for target in claims:
        _corroboration_detail(target, claims, stance_fn=_recorder)
    return found


def enumerate_candidate_pairs(coins: list[str] | None = None) -> dict[str, tuple[str, str]]:
    """對每個幣別各自收集 claims、各自枚舉候選對，再依 `cache_key` 合併成跨幣唯一
    候選對清單（同一份 (a,b) 若剛好在不同幣別重複出現，只保留第一次見到的）。
    """
    coins = coins if coins is not None else COINS
    merged: dict[str, tuple[str, str]] = {}
    for coin in coins:
        claims = collect_claims_for_coin(coin)
        for key, pair in enumerate_candidate_pairs_for_claims(claims).items():
            merged.setdefault(key, pair)
    return merged


def load_existing_cache(path: str | Path) -> dict:
    """讀取既有快取 JSON；不存在或格式錯誤則回空 dict（不拋錯）。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_cache(existing: dict, new_entries: dict) -> dict:
    """merge：保留既有 key，新 key 覆蓋/新增（new_entries 優先）。"""
    merged = dict(existing)
    merged.update(new_entries)
    return merged


def classify_pairs(client: BedrockClient, pairs: dict[str, tuple[str, str]]) -> dict:
    """對每一對呼叫 `client.classify_stance(a, b)`（真呼叫，由呼叫端保證 client
    非 offline），依 `cache_key` 存成 `{"label": ..., "version": STANCE_CACHE_VERSION}`。
    """
    entries: dict = {}
    for key, (a, b) in pairs.items():
        label = client.classify_stance(a, b)
        entries[key] = {"label": label, "version": STANCE_CACHE_VERSION}
        print(f"{a[:40]!r} | {b[:40]!r} -> {label}")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只列候選對，不呼叫 client、不寫檔",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_CACHE_PATH),
        help=f"輸出的 stance_cache.json 路徑（預設 {DEFAULT_CACHE_PATH}）",
    )
    args = parser.parse_args(argv)

    pairs = enumerate_candidate_pairs()
    print(f"候選對數：{len(pairs)}")
    for a, b in pairs.values():
        print(f"  {a[:40]!r} | {b[:40]!r}")

    if args.dry_run:
        print("--dry-run：不呼叫 client、不寫檔。")
        return 0

    client = BedrockClient(offline=False)  # 真 Bedrock client（CEO 親手執行）
    new_entries = classify_pairs(client, pairs)

    existing = load_existing_cache(args.out)
    merged = merge_cache(existing, new_entries)

    Path(args.out).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"已寫入 {args.out}（共 {len(merged)} 筆）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
