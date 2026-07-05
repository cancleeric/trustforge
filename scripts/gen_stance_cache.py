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
4. `--dry-run`：只印出候選對與已快取命中／待呼叫拆分，不呼叫 client、不寫檔。
   否則：先 **fail-closed 讀取既有快取**（`load_existing_cache`：檔案不存在才是
   合法的空 `{}`；存在但讀取/解析失敗或頂層非 dict 一律 raise，避免把「讀不到」
   誤當「本來就是空」，靜默用新資料覆寫掉舊 entry，見 codex 審查發現的第二個
   HIGH），失敗立即中止、不呼叫 client、不寫檔。
   接著（issue #84）用 `select_missing_pairs()` 過濾成「既有快取讀不到」的子集
   `to_call`——已命中的 pair 不重複外呼；`to_call` 為空直接回傳，連真 client 都
   不建立。對 `to_call` 逐一呼叫 `client.classify_stance_strict(a, b)`（真
   Bedrock，非 offline client——**不用**降級版 `classify_stance`：那個方法失敗時
   會吞成 "neutral"，離線批次生成快取分不出「真 neutral」跟「呼叫失敗」，會把假
   neutral 悄悄寫進 `stance_cache.json`、弱化矛盾偵測，見第一個 HIGH）。任一對
   呼叫/解析失敗（strict 版 raise）→ **立即中止，完全不寫檔**（既有快取檔原封
   不動）；真呼叫前也會經 `_make_budget_check()` 檢查 #9 每日 $3 cap，額度不足
   → 提前中止但**保留已成功完成的部分**（見 `BudgetExhausted` docstring，跟真呼叫
   失敗的語意不同）。成功／budget 中止的結果依 `cache_key(a, b)` 存成
   `{"label": label, "version": STANCE_CACHE_VERSION}`，跟既有快取檔 merge（舊
   key 保留，新 key 覆蓋/新增）後原子寫入（temp file + rename，避免寫到一半被
   中斷產生半殘檔）回 `--out`；並把這批真實花費記一筆進跨 run 持久化帳本
   （`_record_batch_cost_to_ledger()`），讓 #9 cap 之後也算得到。

⚠️ 本檔本身不含任何呼叫入口保護以外的巧門——真正打 AWS 只發生在非 --dry-run 且
傳入非 offline 的 `BedrockClient` 時。CEO 親手執行前務必確認環境變數
（`BEDROCK_HAIKU_MODEL_ID` / `AWS_REGION` / AWS 憑證）已就緒。

⚠️ **執行時機（issue #76 已知限制）**：#9 budget guard 的花費追蹤是
**process-local**（帳本快照 + process 內原子預留），不是跨 process 即時共享的
分散式鎖。若本腳本執行期間，另一個 process（例如正在服務中的
`/api/analyze`）也在打真 online stance 呼叫，兩邊互相看不到對方「正在花」的
部分，$3/day cap 有可能被兩邊合計穿破。因此本腳本**應該在維護時段執行，或先
確認當下沒有並行的 online-stance 真流量**（例如暫停對外服務、或至少確認沒有
其他人同時手動執行本腳本/觸發 `/api/analyze`）。跨 process 預算可見性本身不在
本次修復範圍內，見 issue #76。

Issue #84（pre-warm 冪等 + #9 budget guard 硬化）追加：
1. **冪等**：`main()` 先用 `select_missing_pairs()` 把候選對過濾成「既有持久化
   快取讀不到 / version 不符 / label 不合法」的子集才送進 `classify_pairs()`——
   已經命中快取的 pair 不會重複外呼真 Bedrock（見 `select_missing_pairs`
   docstring）。若過濾後已無待呼叫 pair，`main()` 直接回傳 0，連
   `_build_live_client()`（真 boto3 client）都不會建立，確保重複執行零額外 AWS
   呼叫/花費。
2. **budget guard**：真呼叫前用 `_make_budget_check()`（fail-closed 檢查
   `budget_guard.stance_model_priced()`）先擋掉未計價模型；接著
   `classify_pairs()` 對每一次真呼叫另外呼叫
   `budget_guard.try_reserve_request_budget()`／`release_request_budget()`
   做 process-local 原子預留（跟 `pipeline.run()` 同一套 #9 機制），額度不足時
   **呼叫前**就中止剩餘 pair（`BudgetExhausted`，fail-closed，`classify_stance_strict`
   完全不會被執行），不會繞過 cap（不修改 `budget_guard.py` 本身，只組合它既有
   的公開函式）。批次完成後（不論全成功／`BudgetExhausted`／一般失敗，只要真的
   打過 Bedrock）用 `_record_batch_cost_to_ledger()` 把這批真實花費記一筆進跨
   run 持久化帳本（`ledger.append_run()`），讓之後（含隔天前同一天內）
   `/api/analyze` 與再次執行本腳本的 cap 判斷都看得到這筆花費；若這筆記帳本身
   持久化失敗（`append_run()` 回 `False`），改記 `budget_guard.record_unledgered_spend()`
   並在 stderr 明確警示需人工核對 `/costs`，不靜默丟掉。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.bedrock import BedrockClient  # noqa: E402
from trustforge.budget_guard import (  # noqa: E402
    record_unledgered_spend,
    release_request_budget,
    stance_model_priced,
    try_reserve_request_budget,
)
from trustforge.ingestion.base import collect  # noqa: E402
from trustforge.ledger import append_run  # noqa: E402
from trustforge.schema import iso_utc  # noqa: E402
from trustforge.trust.scoring import Claim, _corroboration_detail  # noqa: E402
from trustforge.trust.stance_cache import (  # noqa: E402
    _VALID_LABELS,
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
    """讀取既有快取 JSON——**fail-closed**（codex 審查發現的第二個 HIGH）。

    - 檔案**不存在** → 回 `{}`（正常情境：首次生成快取，沒有舊檔可讀）。
    - 檔案**存在但**讀取失敗（`OSError`，如權限錯誤）/ JSON 解析失敗 / 頂層不是
      dict（如整份是 `[]`）→ **raise**，絕不能悄悄回空 dict！

    原因：呼叫端（`main`）讀到空 dict 後會拿新的候選對 merge 進去，再原子覆寫
    整個檔案——如果這裡把「讀不到」誤當成「本來就是空」，就等於用一份只有 7 對
    新資料的快取，把 CEO/先前 run 累積的所有舊 entry 靜默刪光。「原子寫入」只能
    防止寫到一半損毀，防不了「完整但資料遺失」這種覆寫，所以必須在讀取這一步就
    fail-closed，讓 main 直接中止、完全不寫檔。
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"既有快取檔 {p} 讀取失敗（OSError）：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"既有快取檔 {p} JSON 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"既有快取檔 {p} 頂層結構不是 dict（實際型別：{type(data).__name__}）"
        )
    return data


def merge_cache(existing: dict, new_entries: dict) -> dict:
    """merge：保留既有 key，新 key 覆蓋/新增（new_entries 優先）。"""
    merged = dict(existing)
    merged.update(new_entries)
    return merged


def select_missing_pairs(existing: dict, pairs: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Issue #84 冪等：從候選對 `pairs` 中挑出「既有持久化快取 `existing` 讀不到
    有效結果」的子集——即接下來真的需要外呼 `classify_stance_strict` 的 pair。

    判定「已有效快取」跟 `stance_cache.StanceCache.get()` 共用同一套驗證
    （`entry.get("version") == STANCE_CACHE_VERSION` **且**
    `entry.get("label") in _VALID_LABELS`）——`existing[key]` 不是 dict、缺
    `version`、version 不符（prompt/model 版本已變更，見 `stance_cache.py`
    模組頂部說明）、或 `label` 不是合法值（壞資料，如手動編輯打錯字/舊格式
    殘留）都視為需要重新分類。

    codex/harper HIGH（#84 review）修正：先前這裡只驗 `version`、不驗
    `label`——一筆 `version` 相符但 `label` 非法的壞 entry，會被本函式當成
    「已快取命中」永久跳過（重跑幾次都不會被重新分類/修正），但 runtime
    真正讀取快取的 `stance_cache.StanceCache.get()` 卻會判定同一筆 `label`
    不合法而回 `None`（miss，fail-safe 降級 "neutral"）——兩套判準
    drift，讓這筆壞資料在 pre-warm 眼中「永遠已快取」、卻在真正服務時每次
    都要重新 fail-safe，且無法透過重跑本腳本自我修復。改成兩者共用
    `_VALID_LABELS` 這一份驗證，杜絕 drift。

    回傳的子集依然是 `{cache_key: (a, b)}`，可直接餵給 `classify_pairs()`——
    呼叫端（`main()`）藉此確保**已經命中快取的 pair 不會重複外呼真 Bedrock**，
    重複執行本腳本時只會對真正缺漏/過期/壞掉的 pair 花錢，冪等（見 issue #84）。
    """
    missing: dict[str, tuple[str, str]] = {}
    for key, pair in pairs.items():
        entry = existing.get(key)
        if (
            isinstance(entry, dict)
            and entry.get("version") == STANCE_CACHE_VERSION
            and entry.get("label") in _VALID_LABELS
        ):
            continue  # 已快取命中，version 相符且 label 合法，本輪不重複外呼
        missing[key] = pair
    return missing


class BudgetExhausted(Exception):
    """Issue #84：`classify_pairs()` 的 `budget_check` 回 False（#9 每日 $3 cap
    即將/已經用盡）時提前中止批次的內部信號。

    跟「真呼叫失敗」（如 `classify_stance_strict` 逾時/憑證錯誤 raise）刻意分開
    處理：那種情況是「這對到底是什麼立場我們不知道」，必須整批中止、完全不寫檔，
    避免把不確定結果悄悄當成 neutral 寫入持久化快取。budget 提前中止則不同——
    已經呼叫成功的每一筆 `entries` 都是真實、正確的分類結果（錢已經花了），沒有
    任何理由丟棄；反而應該保留寫入，讓下次重跑（隔天 cap 重置或人工調高後）能
    透過 #84 冪等機制跳過這些已完成的 pair，不重複外呼、不重複花錢。
    """

    def __init__(self, entries: dict):
        super().__init__(f"budget 中止：本次批次已完成 {len(entries)} 筆分類")
        self.entries = entries


def _make_budget_check(client: BedrockClient):
    """組合既有 `budget_guard` 公開函式，回傳一個 `() -> bool` closure，供
    `classify_pairs()` 在每次真呼叫前先做一層「model 是否已計價」檢查。

    codex/harper HIGH（#84 review 必修 2）修正：這裡原本直接比較「帳本快照
    已花費 + 本批次 in-flight `client.cost_events`」跟 cap，是**唯讀 peek**——
    在額度逼近上限（例如只剩 $0.001）時仍可能誤判「還有餘裕」而放行下一次
    真呼叫，讓 $3/day cap 被穿破；同一個 process 若同時有其他呼叫者在跑，
    也完全看不到彼此「正在花」的部分（check-then-spend race）。

    「今日 cap 是否還有餘裕」現在改由 `classify_pairs()` 對**每一次真呼叫**
    呼叫 `budget_guard.try_reserve_request_budget()` 做 process-local 原子
    預留來把關——跟 `pipeline.run()` 保護 `/api/analyze` 用的**同一套**機制
    （#9 本來就是為此而建），才是唯一的 authority；這裡不再重複判斷同一件
    事，避免兩套邏輯彼此 drift。

    這裡只保留 `stance_model_priced()`：這次會用到的 stance model 若未在
    `ledger.PRICING` 計價表登記，真實單價未知，`try_reserve_request_budget()`
    的固定保守估值（`request_max_cost_usd()`）就不再是可信的「上界」，必須
    fail-closed 直接視為不放行（比照 `pipeline.run()` 對 unpriced model 的
    處理）——這是原子預留機制本身不檢查、獨立於「有沒有錢」的面向。`client`
    參數保留只是維持既有呼叫簽章（`_make_budget_check(client)`），目前
    未在函式本體內使用。
    """

    def _check() -> bool:
        return stance_model_priced()

    return _check


def _record_batch_cost_to_ledger(client: BedrockClient, *, now_fn=time.time) -> None:
    """把本次批次真的花掉的 Bedrock 成本（`client.cost_events`）記一筆進跨 run
    持久化帳本（`ledger.append_run()`），讓 #9 `daily_cap_exceeded()` /
    `daily_cost_usd()` 之後（含 `/api/analyze` 與本腳本下次執行）查詢「今日已
    花費」時也算得到這批 pre-warm 花費——否則這筆真實花費對預算護欄完全隱形，
    等於繞過 $3/day cap（issue #84 CEO 追加要求）。

    duck-typed：`client` 沒有 `cost_events` 屬性（單元測試常用的假 client）
    → 直接 no-op，不影響既有測試。`cost_events` 為空或總花費為 0（如整批全部
    命中既有快取、本輪完全沒有真呼叫）也不寫，避免帳本塞入無意義的 $0 空記錄。

    codex HIGH / harper LOW（#84 review 必修 3）修正：比照 `orchestrator.py`
    的既有慣例——`append_run()` 回傳值代表「這筆真的有花錢的紀錄有沒有真的
    持久化成功」（可能因磁碟/檔案系統問題連 fallback 都寫失敗），**不能靜默
    丟掉**。回 `False` 時呼叫 `budget_guard.record_unledgered_spend()` 讓這筆
    花費至少在 process 記憶體內被 fail-closed 記住（`daily_cost_usd()` 之類
    的讀取路徑才不會漏算），並在 stderr 印出明確警示，提醒之後要人工核對
    `/costs`。
    """
    cost_events = getattr(client, "cost_events", None)
    if not cost_events:
        return
    calls = [
        {
            "model": e.get("model"),
            "tokens_in": e.get("tokens_in", 0),
            "tokens_out": e.get("tokens_out", 0),
            "cost_usd": e.get("cost_usd", 0.0),
        }
        for e in cost_events
    ]
    total_cost = round(sum(c["cost_usd"] for c in calls), 6)
    if total_cost <= 0:
        return
    persisted = append_run({
        "ts": iso_utc(now_fn()),
        "question_type": "stance_prewarm",
        "coin": "MULTI",
        "offline": False,
        "calls": calls,
        "total_cost_usd": total_cost,
    })
    if not persisted:
        record_unledgered_spend(total_cost)
        print(
            f"警告：本批次 pre-warm 花費 ${total_cost} 未能寫入帳本"
            "（append_run 失敗，含 fallback），此筆花費未進帳本，人工核對 /costs。",
            file=sys.stderr,
        )


def classify_pairs(
    client: BedrockClient,
    pairs: dict[str, tuple[str, str]],
    *,
    budget_check=None,
) -> dict:
    """對每一對呼叫 `client.classify_stance_strict(a, b)`（**嚴格版**，真呼叫，由
    呼叫端保證 client 非 offline），依 `cache_key` 存成
    `{"label": ..., "version": STANCE_CACHE_VERSION}`。

    ⚠️ 刻意用 `classify_stance_strict` 而非降級版 `classify_stance`：批次生成
    持久化快取時，「呼叫失敗」必須跟「模型真的判斷 neutral」明確分開，否則會把
    假 neutral 悄悄寫進 `stance_cache.json`、弱化矛盾偵測（見 codex 審查 HIGH）。

    任一對呼叫/解析失敗 → `classify_stance_strict` raise，這裡**不 catch**、直接
    往上傳給呼叫端（`main()`），讓整批「全成功才寫」的語意成立：只要有一對失敗，
    這個函式就不會回傳完整的 entries dict，呼叫端也就不會走到 merge + 寫檔那步。

    `budget_check`：選用的 `() -> bool` callable（見 `_make_budget_check()`），
    每次真呼叫前呼叫一次；回 `False`（如 stance model 未計價）→ 立即中止，
    拋出 `BudgetExhausted(entries)`（攜帶目前已成功完成的部分），**不呼叫**
    `classify_stance_strict`，也不當成「呼叫失敗」處理（見 `BudgetExhausted`
    docstring：兩者收尾語意不同）。預設 `None`（不檢查任何預算，逐字沿用
    加入 #9 budget guard 整合前的行為，供既有單元測試直接呼叫
    `classify_pairs(client, pairs)` 不受影響）。

    codex/harper HIGH（#84 review 必修 2）追加：`budget_check` 不是 `None`
    時（呼叫端明確要求受 #9 cap 約束——`main()` 一律如此），除了上面那次
    `budget_check()` 檢查，每次真呼叫前**另外**呼叫
    `budget_guard.try_reserve_request_budget()` 做 process-local 原子預留
    （跟 `pipeline.run()` 保護 `/api/analyze` 用的同一套機制）：拿不到預留
    （額度已被其他 in-flight 呼叫佔滿，或帳本讀取失敗 fail-safe）一律視為
    `BudgetExhausted`——**呼叫前**就中止，`classify_stance_strict` 完全不會
    被執行。拿到預留後，不論這次呼叫成功與否，都在 `finally` 立刻
    `budget_guard.release_request_budget()` 釋放（真實花費另由呼叫端的
    `_record_batch_cost_to_ledger()` 記帳，這裡的「預留」只是暫時佔位，不是
    重複計費，跟 `pipeline.run()` 同慣例）。`budget_check is None` 時完全不
    做任何預留，逐字沿用未整合 budget guard 前的行為。
    """
    entries: dict = {}
    for key, (a, b) in pairs.items():
        reservation: float | None = None
        if budget_check is not None:
            if not budget_check():
                raise BudgetExhausted(entries)
            reservation = try_reserve_request_budget()
            if reservation is None:
                raise BudgetExhausted(entries)
        try:
            label = client.classify_stance_strict(a, b)
        finally:
            if budget_check is not None:
                release_request_budget(reservation)
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


_MAINTENANCE_WINDOW_WARNING = (
    "警告：#9 budget guard 是 process-local 追蹤（帳本快照 + process 內原子預留），"
    "不是跨 process 即時共享的分散式鎖（issue #76 已知限制）。請在維護時段執行，"
    "或先確認當下沒有並行的 online-stance 真流量（例如同時有人在跑 /api/analyze），"
    "否則 $3/day cap 有可能被兩邊合計穿破。"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "",
        epilog=_MAINTENANCE_WINDOW_WARNING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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

    # 先讀既有快取（fail-closed，見 load_existing_cache docstring）：確保寫檔前
    # 一定拿得到「真正的舊資料」，也讓壞檔案能在花真 Bedrock 呼叫之前就先中止，
    # 不浪費真呼叫的成本/額度。`--dry-run` 也需要讀這個，才能秀出「已快取命中／
    # 待呼叫」拆分（issue #84 hit rate 量測的一部分）。
    try:
        existing = load_existing_cache(args.out)
    except Exception as exc:
        print(
            f"錯誤：既有快取檔讀取失敗，中止且不寫檔，既有快取保持不變：{exc}",
            file=sys.stderr,
        )
        return 1

    # issue #84 冪等：只有「既有快取讀不到有效結果」的 pair 才需要真呼叫。
    to_call = select_missing_pairs(existing, pairs)
    _hit = len(pairs) - len(to_call)
    _hit_rate_pct = (_hit / len(pairs) * 100) if pairs else 100.0
    print(f"已快取命中：{_hit}／{len(pairs)}（{_hit_rate_pct:.1f}%），需要真呼叫：{len(to_call)}")

    if args.dry_run:
        print("--dry-run：不呼叫 client、不寫檔。")
        return 0

    if not to_call:
        print("全部候選對皆已快取命中，本輪無需任何真呼叫（issue #84 冪等：不重複外呼）。")
        return 0

    client = _build_live_client()  # 真 Bedrock client（CEO 親手執行）
    budget_check = _make_budget_check(client)
    _budget_exhausted = False
    _classify_failed = False
    new_entries: dict = {}
    try:
        new_entries = classify_pairs(client, to_call, budget_check=budget_check)
    except BudgetExhausted as exc:
        # #9 每日 $3 cap 額度不足：提前中止，但保留已成功完成的部分（見
        # BudgetExhausted docstring）——跟真呼叫失敗（下方 except Exception）
        # 刻意分開處理，不當成錯誤。
        new_entries = exc.entries
        _budget_exhausted = True
        print(
            f"警告：#9 每日 Bedrock 預算即將用盡，批次提前中止，已保留 "
            f"{len(new_entries)}／{len(to_call)} 筆成功結果（其餘可待額度恢復後重跑，"
            "已完成的 pair 不會重複外呼）。",
            file=sys.stderr,
        )
    except Exception as exc:
        # 任一對失敗 → 中止且完全不寫檔，既有快取保持不變（見 classify_pairs docstring）。
        _classify_failed = True
        print(
            f"錯誤：分類失敗，中止且不寫檔，既有快取保持不變：{exc}",
            file=sys.stderr,
        )
    finally:
        # harper/codex HIGH（#84 review 必修 1）：不論全成功／BudgetExhausted／
        # 一般失敗，只要這次真的呼叫過 Bedrock（`client.cost_events` 非空——即使
        # 是失敗前已成功的前 k 筆），這筆真實花費都必須先記回帳本，否則下面
        # `_classify_failed` 為真時直接 `return 1`（不寫 cache 檔）會連帶把「已經
        # 真的花掉的錢」也從帳本抹除，讓 #9 cap 之後看不到這筆真實支出（見
        # `_record_batch_cost_to_ledger` docstring；no-op：空 cost_events/假
        # client，不影響既有測試）。
        _record_batch_cost_to_ledger(client)

    if _classify_failed:
        return 1

    merged = merge_cache(existing, new_entries)
    atomic_write_json(args.out, merged)
    print(f"已寫入 {args.out}（共 {len(merged)} 筆）")
    return 2 if _budget_exhausted else 0


if __name__ == "__main__":
    raise SystemExit(main())
