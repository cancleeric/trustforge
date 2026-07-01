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
   否則：對每一對呼叫 `client.classify_stance_strict(a, b)`（真 Bedrock，非 offline
   client——**不用**降級版 `classify_stance`：那個方法失敗時會吞成 "neutral"，
   離線批次生成快取分不出「真 neutral」跟「呼叫失敗」，會把假 neutral 悄悄寫進
   `stance_cache.json`、弱化矛盾偵測，見 codex 審查發現的 HIGH）。任一對呼叫/解析
   失敗（strict 版 raise）→ **立即中止，完全不寫檔**（既有快取檔原封不動）；
   全部 7 對都成功才依 `cache_key(a, b)` 存成 `{"label": label, "version":
   STANCE_CACHE_VERSION}`，跟既有快取檔 merge（舊 key 保留，新 key 覆蓋/新增）後
   原子寫入（temp file + rename，避免寫到一半被中斷產生半殘檔）回 `--out`。

⚠️ 本檔本身不含任何呼叫入口保護以外的巧門——真正打 AWS 只發生在非 --dry-run 且
傳入非 offline 的 `BedrockClient` 時。CEO 親手執行前務必確認環境變數
（`BEDROCK_HAIKU_MODEL_ID` / `AWS_REGION` / AWS 憑證）已就緒。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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
    """對每一對呼叫 `client.classify_stance_strict(a, b)`（**嚴格版**，真呼叫，由
    呼叫端保證 client 非 offline），依 `cache_key` 存成
    `{"label": ..., "version": STANCE_CACHE_VERSION}`。

    ⚠️ 刻意用 `classify_stance_strict` 而非降級版 `classify_stance`：批次生成
    持久化快取時，「呼叫失敗」必須跟「模型真的判斷 neutral」明確分開，否則會把
    假 neutral 悄悄寫進 `stance_cache.json`、弱化矛盾偵測（見 codex 審查 HIGH）。

    任一對呼叫/解析失敗 → `classify_stance_strict` raise，這裡**不 catch**、直接
    往上傳給呼叫端（`main()`），讓整批「全成功才寫」的語意成立：只要有一對失敗，
    這個函式就不會回傳完整的 entries dict，呼叫端也就不會走到 merge + 寫檔那步。
    """
    entries: dict = {}
    for key, (a, b) in pairs.items():
        label = client.classify_stance_strict(a, b)
        entries[key] = {"label": label, "version": STANCE_CACHE_VERSION}
        print(f"{a[:40]!r} | {b[:40]!r} -> {label}")
    return entries


def atomic_write_json(path: str | Path, data: dict) -> None:
    """原子寫入 JSON：先寫 temp file 再 `os.replace` rename，避免寫到一半被中斷
    （或跟其他 process 競爭）留下半殘的快取檔。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _build_live_client() -> BedrockClient:
    """建立真 Bedrock client（CEO 親手執行用）。獨立成函式方便測試 monkeypatch
    替換成假 client，不必真的建立/呼叫 boto3。
    """
    return BedrockClient(offline=False)


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

    client = _build_live_client()  # 真 Bedrock client（CEO 親手執行）
    try:
        new_entries = classify_pairs(client, pairs)
    except Exception as exc:
        # 任一對失敗 → 中止且完全不寫檔，既有快取保持不變（見 classify_pairs docstring）。
        print(
            f"錯誤：分類失敗，中止且不寫檔，既有快取保持不變：{exc}",
            file=sys.stderr,
        )
        return 1

    existing = load_existing_cache(args.out)
    merged = merge_cache(existing, new_entries)
    atomic_write_json(args.out, merged)
    print(f"已寫入 {args.out}（共 {len(merged)} 筆）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
