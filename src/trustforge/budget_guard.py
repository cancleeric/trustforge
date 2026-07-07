"""#9 online-stance 預算配額硬化 — 開 Bedrock 給公開 `/api/analyze` 前的護欄。

背景：老闆授權開 Bedrock，但 `/api/analyze` 是公開端點，必須先把「公開流量
燒爆 token/成本」的總閘門立起來。本模組只做兩件事，兩者互相獨立、任一項
未啟用都不影響既有行為：

1. **每日全域成本上限**（`daily_cap_exceeded`）：讀既有跨 run 持久化成本
   帳本（`ledger.get_ledger()`），彙總「今日」（UTC 日期）所有 run 的
   `total_cost_usd`，跟生效 cap（admin console PR-3 起走三層 fallback：
   **config store → env `TRUSTFORGE_BEDROCK_DAILY_USD_CAP` → DEFAULT $3**，
   見 `daily_cap_usd_resolved()`——決賽當天可臨時經管理面/env 調整，
   **不寫死**）比較。達標即回
   `True`，呼叫端（`pipeline.run`）據此把整輪強制降級成離線 abstain，直到
   隔日 UTC 重置（帳本按日期彙總，天然重置，不需額外狀態）。

2. **online-stance 獨立開關**（`online_stance_requested`）：online-stance
   （跨源語意矛盾判斷用的 stance 分類呼叫）要能在「敘事本身仍離線」
   （公開預設 `data_mode=live, llm_mode=off`，即 `?real=1` 檔位）時也用上
   真 Bedrock，需要**額外**明確開這個開關（`TRUSTFORGE_ONLINE_STANCE` 為
   truthy）加上已設定真 `BEDROCK_MODEL_ID`，兩者缺一則維持離線（fail-safe
   回 "neutral"／讀持久化快取），跟加入本模組前逐字相同。既有
   `?live=1&token=<TOKEN>` 全量敘事模式（`llm_mode=bedrock`）不受這個開關
   影響——那條路徑本來就已經是真 Bedrock，stance 自然跟著走真呼叫（向後
   相容）。
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timezone

from . import admin_config
from .ledger import PRICING, Ledger, get_ledger

# stance model 未設定 `BEDROCK_HAIKU_MODEL_ID` 時的預設值，必須跟
# `bedrock.BedrockConfig.stance_model_id` 的預設值逐字一致——這裡刻意不透過
# `BedrockConfig()` 讀取（dataclass 欄位預設值 `os.getenv(...)` 只在模組
# **匯入當下**求值一次，之後不會因為 `BEDROCK_HAIKU_MODEL_ID` 環境變數改變
# 而更新，會讓「這次請求實際會用的 model」判斷讀到匯入當時的舊值），
# 改為每次呼叫都直接讀 env，確保 fail-closed 判斷永遠反映當下設定。
_DEFAULT_STANCE_MODEL_ID = "au.anthropic.claude-haiku-4-5-20251001-v1:0"

_log = logging.getLogger(__name__)

# 每日全域 Bedrock 成本上限（USD）預設值。刻意保守（保護公開流量），
# 環境變數 `TRUSTFORGE_BEDROCK_DAILY_USD_CAP` 可覆寫——不要在這裡改寫死
# 數字本身，決賽現場可能需要臨時調整（如當天調高到 $30）。
DEFAULT_BEDROCK_DAILY_USD_CAP = 3.0

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _env_cap() -> float | None:
    """env 層 `TRUSTFORGE_BEDROCK_DAILY_USD_CAP` 解析（既有語意逐字保留）：
    未設定 / 空字串 / 無法解析 / 非有限（NaN/Inf，codex HIGH）→ `None`
    （呼叫端落下一層）；其餘回 float——**含 ≤0＝緊急全關訊號**（由
    `daily_cap_exceeded` 的 `cap<=0` 分支處理）。"""
    raw = os.getenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP")
    if raw is None or not raw.strip():
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    if not math.isfinite(val):
        return None
    return val


def daily_cap_usd_resolved() -> tuple[float, str]:
    """每日 cap 的三層 fallback 解析（admin console PR-3，計劃 §5）：
    **config store → env → DEFAULT（$3）**，每層「未設定/壞值」落下一層。
    回傳 `(生效值, 來源)`，來源 ∈ `"config"` / `"env"` / `"default"`——
    `GET /api/admin/config` 的 `effective`/`source` 欄位**必須**呼叫本函式
    取值（單一事實來源：顯示的生效值就是分析路徑真的會用的值，不得各自
    重算，避免兩邊邏輯日後分岔）。

    ⚠️ **例外：env cap≤0＝kill-switch，最高優先短路**（PR-3 複審 harper
    M2）：env `TRUSTFORGE_BEDROCK_DAILY_USD_CAP` 解析出 ≤0 時**直接生效**
    （回 `(該值, "env")`，不讀 config）——「env 設 cap≤0＝緊急全關」是
    既有維運 SOP 的逃生口，不能被後來才加的 config 層遮蔽（否則 config
    已設 cap 的部署，緊急時設 env=0 會被 config 蓋掉、kill-switch 失效）。
    短路同時意味著 kill-switch 生效期間**零 DynamoDB 讀**（緊急關閉不
    依賴管理面存活）。config 層本來就不允許 cap≤0 落庫（PR-1/PR-2 雙重
    驗證），語意不衝突。

    1. **config 層**（`admin_config.get_config_cached_failsoft()`，15s TTL
       快取 + 15s 失敗負快取（PR-3 複審 qa M1：DynamoDB 持續故障時本函式
       每個 analyze 請求都會走到，沒有負快取就每請求重付一次有界 timeout
       （~12s）的失敗讀）；本 process 的 `put_config()` write-through 立即
       生效）：
       - `daily_cap_usd` 已設定（`_parse_cap` 已保證 finite float）→ 生效。
         PR-1 儲存層 + PR-2 API 層雙重驗證都不允許負值落庫（cap≤0 的
         「緊急全關」語意專屬 env 層，config 層走 `bedrock_enabled=false`
         做緊急關閉），與 env 層語意不衝突。
       - item 不存在 / 欄位未設定 / 壞欄（`admin_config._parse_cap` 逐欄
         容錯回 None）→ 落 env 層，行為與 v0.7.0 逐字相同。
       - **讀取異常**（`AdminConfigReadError`：網路/憑證/throttle/負快取
         窗內——不是「未設定」）→ log warning + 落 env 層。管理面故障
         不能讓分析路徑整個掛掉；env 層是部署當下就存在的可信 fallback
         （計劃 §5）。
       ⚠️ 執行緒安全：`get_config_cached*()` 的 cache-miss DynamoDB 讀在
       admin_config 自己的鎖外執行、自帶有界 timeout（3s/3s/2 attempts）；
       本函式的呼叫端 `BudgetReservation.try_reserve` 在**進 `_lock` 之前**
       就完成 `daily_cap_usd()`，慢網路讀絕不會發生在 `_lock` 內。
    2. **env 層** `TRUSTFORGE_BEDROCK_DAILY_USD_CAP`：語意逐字保留（解析
       見 `_env_cap`；cap≤0 已在最高優先短路生效，不會走到這裡才生效）。
       未設定 / 空字串 / 無法解析 / 非有限 → 落 DEFAULT。
    3. **DEFAULT**：`DEFAULT_BEDROCK_DAILY_USD_CAP`（$3）。
    """
    env_val = _env_cap()
    # --- env kill-switch 最高優先短路（harper M2，見 docstring）---
    if env_val is not None and env_val <= 0:
        return env_val, "env"
    # --- config 層 ---
    try:
        cfg = admin_config.get_config_cached_failsoft()
    except Exception:
        _log.warning(
            "[budget_guard] admin config 讀取失敗，daily cap 落 env 層"
            "（管理面故障不影響分析路徑；env/DEFAULT 為可信 fallback）",
            exc_info=True,
        )
    else:
        if cfg.daily_cap_usd is not None:
            # `admin_config._parse_cap` 已保證 finite；負值被 PR-1 儲存層
            # + PR-2 API 層雙重擋下不可能落庫，不需在此重複判斷。
            return cfg.daily_cap_usd, "config"
    # --- env 層 ---
    if env_val is not None:
        return env_val, "env"
    return DEFAULT_BEDROCK_DAILY_USD_CAP, "default"


def daily_cap_usd() -> float:
    """生效的每日 cap（USD）——`daily_cap_usd_resolved()` 的值部分。三層
    fallback（config → env → DEFAULT $3）見該函式 docstring。既有呼叫端
    （`daily_cap_exceeded`/`BudgetReservation.try_reserve`）介面不變。"""
    return daily_cap_usd_resolved()[0]


# ---------------------------------------------------------------------------
# codex HIGH 追加（記帳完整性）：append 失敗吞掉 → 花費沒記 → cap 失效
# ---------------------------------------------------------------------------
# 背景：`ledger.append_run()` 是「帳本是 pipeline 的旁路，寫失敗不能讓已經
# 算完的分析中斷」這個既有設計的必然結果——primary（DynamoDB）+ fallback
# （JsonlLedger）都失敗時，`append_run()` 只印 stderr warning、吞掉例外，
# **這次真的已經花掉的 Bedrock 成本從未進到帳本**。`daily_cost_usd()` 的
# authority 完全建立在這份 best-effort 帳本上——一筆花費沒記進去，
# `daily_cap_exceeded()`/`try_reserve_request_budget()` 就永遠看不到它，
# 等於這筆花費對 cap 而言「沒發生過」。若當下 storage 唯讀/滿/不可用是
# 持續性故障（不是單筆偶發），後續每個重複請求都會被 guard 誤判成「還有
# 預算」，讓 $3/day cap 在記帳持續失敗期間形同虛設、可被無限次重複請求
# 繞過（真實花費照樣持續發生，只是全部沒進帳本）。
#
# 修法（in-memory fail-closed 保留，見 follow-up #76 的最終解）：
# `append_run()` 改回傳 `bool`（是否真的持久化成功，primary 或 fallback
# 任一成功即算）；持久化失敗時，呼叫端（`orchestrator.py` 收尾）把這筆
# 「真的花了、但沒記進帳本」的金額丟進這裡的 process-local
# `_UnledgeredSpend` 計數器；`daily_cost_usd()` 把這個計數器也算進「今日
# 已花費」——即使帳本本身讀不到這筆，guard 依然把它算在 cap 裡，不會被
# 重複請求繞過。process 重啟後計數器歸零可接受：重啟代表沒有 in-flight
# 請求、且 primary+fallback 同時持續失敗本就是罕見的雙重故障情境；真正
# 的長期解法是用原子持久 budget counter（DynamoDB atomic UpdateItem）
# 取代「從 best-effort 帳本重建 authority」，另立 follow-up（#76）追蹤，
# 不在本次修復範圍內。
class _UnledgeredSpend:
    """process-local 計數器：累加「真的已經花掉、但 `ledger.append_run()`
    回報持久化失敗」的成本，供 `daily_cost_usd()` 一併算進「今日已花費」。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0.0

    def add(self, amount: float) -> None:
        if amount is None or not math.isfinite(amount) or amount <= 0:
            return
        with self._lock:
            self._total = round(self._total + amount, 6)

    def total(self) -> float:
        with self._lock:
            return self._total


_UNLEDGERED_SPEND = _UnledgeredSpend()


def record_unledgered_spend(amount: float) -> None:
    """`amount`（USD）這次真的已經花掉、但 `ledger.append_run()` 回報
    persist 失敗（primary+fallback 皆失敗）——fail-closed 記到 process-local
    計數器，讓 `daily_cost_usd()` 把它算進「今日已花費」，不會因為帳本沒
    記到就被當作沒發生（否則重複請求會一直看到「未用預算」，可無限
    繞過 $3/day cap）。呼叫端：`agent.orchestrator.run_agent_pipeline()`
    收尾，`append_run()` 回傳 `False` 時。"""
    _UNLEDGERED_SPEND.add(amount)


def _reset_unledgered_spend_for_tests() -> None:
    """測試專用：把 process-local 未記帳花費計數器歸零，避免跨測試殘留
    （比照 `_reset_reservation_for_tests()`）。"""
    with _UNLEDGERED_SPEND._lock:
        _UNLEDGERED_SPEND._total = 0.0


def daily_cost_usd(
    ledger: Ledger | None = None, *, now_fn=time.time, day: str | None = None
) -> float:
    """今日（UTC 日期，`YYYY-MM-DD`）累計 Bedrock 成本（USD）。

    彙總依據：跨 run 持久化帳本每筆紀錄的 `ts`（`agent.orchestrator` 收尾寫入
    的 ISO UTC 字串，見 `ledger.append_run` 呼叫點）所屬日期是否等於今天，
    相符則累加該筆的 `total_cost_usd`。壞資料（`ts` 缺失/格式異常、
    `total_cost_usd` 非數字）一律當 0 處理，不讓單筆壞紀錄拖垮整體彙總
    （比照 `Ledger.summary()` 既有的容錯慣例）。

    codex HIGH 追加（記帳完整性）：另外加上 `_UNLEDGERED_SPEND`——那些真的
    花掉、但 `append_run()` 沒能持久化進帳本的成本（見上方區塊說明）。
    這筆不分日期，只要還沒被 process 重啟清空，就一律算進當下查詢的
    「今日已花費」（保守：寧可多算、不願少算，fail-closed）。
    """
    if ledger is None:
        ledger = get_ledger()
    if day is None:
        day = datetime.fromtimestamp(now_fn(), tz=timezone.utc).date().isoformat()
    total = 0.0
    for rec in ledger.read_all():
        ts = str(rec.get("ts", ""))
        if ts[:10] != day:
            continue
        try:
            val = float(rec.get("total_cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val) or val < 0:
            # codex HIGH：float("nan")/inf 不拋錯——NaN/Inf/負值帳本記錄會讓
            # total 變 NaN，之後 spent>=cap 恆 false → cap fail-open 全放行。
            # 帳本記帳出現非有限/負值 = 記帳失敗、總額不可信 → fail-closed:
            # raise 讓上層(daily_cap_exceeded/try_reserve 的 except)走 fail-safe
            # 強制離線(不花 Bedrock、安全)。
            raise ValueError(f"non-finite/negative ledger cost: {val!r}")
        total += val
    return round(total + _UNLEDGERED_SPEND.total(), 6)


def daily_cap_exceeded(ledger: Ledger | None = None, *, now_fn=time.time) -> bool:
    """全域每日 Bedrock 成本上限是否已達標。

    `cap <= 0`：視為「本日完全不放行任何 Bedrock 花費」（保護性解讀，供
    緊急關閉使用），直接回 `True`，不需先讀帳本。

    達標時，呼叫端（`pipeline.run`）應把本次（及當日後續）整輪強制降級為
    離線 abstain（narrative + stance 皆不再打真 Bedrock），直到隔日 UTC
    帳本彙總自然歸零重置——不需要額外的重置狀態機。
    """
    cap = daily_cap_usd()
    if cap <= 0:
        return True
    try:
        spent = daily_cost_usd(ledger, now_fn=now_fn)
    except Exception:
        # fail-safe（codex HIGH）：成本帳本讀取失敗（DynamoDB 憑證/網路/
        # throttle/table outage）時，**不能**讓成本帳本這個非關鍵 side-channel
        # 的故障連帶把分析請求整個打掛（append 路徑本就把帳本當非關鍵）。
        # 但也不能在無法確認預算的情況下放行真 Bedrock 花費——所以保守地
        # 視為「已達每日上限」→ 呼叫端強制本輪離線 abstain（不花 Bedrock、
        # 安全），分析仍照常回傳（離線）。附可觀測 log。
        _log.warning(
            "[budget_guard] 每日成本帳本讀取失敗，fail-safe 強制本輪 Bedrock 離線（分析照常走離線）",
            exc_info=True,
        )
        return True
    return spent >= cap


# ---------------------------------------------------------------------------
# codex HIGH 追加（並行 TOCTOU）：process-local 原子預留
# ---------------------------------------------------------------------------
# 背景：`daily_cap_exceeded()` 讀的是「已完成 run」的歷史帳本彙總，但真實
# 成本要等 pipeline 跑完才由 `orchestrator.py` 收尾 `ledger.append_run()`
# 寫回。公開 `/api/analyze` 走 `ThreadingHTTPServer`（見 web.py，每請求一條
# 執行緒、同 process），N 個並行請求在同一瞬間呼叫 `daily_cap_exceeded()`
# 時看到的是同一份「今日已花費」總額（因為彼此都還沒寫回）——check-then-
# spend race，$3/day 硬上限被並行撐爆成無界倍數。
#
# 修法（現生產是單一 EC2／單 process，process-local 鎖即足夠；多實例部署
# 才需要跨行程的 DynamoDB conditional write，見下方 follow-up 說明）：
# 呼叫 Bedrock 前先在鎖內把「已花費（讀帳本）+ 目前所有 in-flight 請求已
# 預留的總額 + 這次請求的保守成本上界」跟 cap 比較，還有空間才准許把這次
# 的保守上界疊加進 `reserved`；請求結束（成功或失敗皆同）再釋放。這樣
# 並行請求彼此能看到對方「正在花」的部分，不再只看到同一份歷史總額。

DEFAULT_REQUEST_MAX_USD = 0.05
"""單次 `/api/analyze` 請求「最壞情況」可能觸發的 Bedrock 花費保守上界
（USD）。寧可估高擋掉合法請求，也不能低估讓並行 race 有機可乘。

估算依據：
- narrative 敘事呼叫最多 1 次：`BEDROCK_MAX_TOKENS`（預設 1024）輸出，用
  `ledger.PRICING` 最貴的模型單價（sonnet-4-6：input $3/1M、output
  $15/1M）估算，單次約 $0.02～$0.03。
- online-stance 分類呼叫（`scoring.py` O(n²) 迴圈、cache-miss 才真打，
  maxTokens=128、較便宜的 haiku 單價）同一請求可能觸發多筆，疊一截安全
  邊際。
兩者加總取整到 $0.05。可用 `TRUSTFORGE_BEDROCK_REQUEST_MAX_USD`（env）
覆寫——不寫死，決賽現場依 `/costs` 觀察到的真實單次花費調整。
"""


def request_max_cost_usd() -> float:
    """讀 `TRUSTFORGE_BEDROCK_REQUEST_MAX_USD`（USD）。未設定 / 空字串 / 無法
    解析成浮點數 → 回傳保守預設值 `DEFAULT_REQUEST_MAX_USD`（$0.05）。"""
    raw = os.getenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD")
    if raw is None or not raw.strip():
        return DEFAULT_REQUEST_MAX_USD
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_REQUEST_MAX_USD
    if not math.isfinite(val) or val < 0:
        # NaN/Inf/負值 env → 用保守預設(codex HIGH:別讓壞 env 破壞 cap)
        return DEFAULT_REQUEST_MAX_USD
    return val


class BudgetReservation:
    """Process-local 原子「預留額度」計數器，修復 #9 codex HIGH 並行 TOCTOU。

    只在單一 process 內有效（`threading.Lock` 保護的記憶體計數器）——現生產
    單一 EC2 部署下即足夠擋住同 process 內多執行緒的並行 race。多實例
    （多 process／多機器）部署時，各 process 各自的 `reserved` 互不可見，
    仍會退化回原本的 race；那種情況需要換成跨行程的原子操作（例如
    DynamoDB conditional write：`UpdateItem` 搭配
    `ConditionExpression="reserved_plus_spent <= cap"`），留作 follow-up
    （見 issue），本類別明確只解「單 process」範圍。

    Process 重啟時 `reserved` 歸零：重啟當下不可能還有任何 in-flight 請求
    等著 `release()`，歸零是安全的——已花費的部分永遠另外讀帳本
    （`daily_cost_usd()`），不會因為 reserved 歸零而漏算。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved = 0.0

    def try_reserve(
        self, ledger: Ledger | None = None, *, now_fn=time.time
    ) -> float | None:
        """嘗試原子預留這次請求的保守成本上界。

        成功回傳「已預留金額」（後續 `release()` 要原封傳回同一個值）；
        額度不足，或帳本讀取失敗（fail-safe，跟 `daily_cap_exceeded()` 同
        一套保守邏輯：無法確認今日已花費就不能放行新的 Bedrock 花費），
        一律回傳 `None`——呼叫端應把這次請求視同「已達每日上限」處理
        （強制離線 abstain，不放行真 Bedrock）。
        """
        cap = daily_cap_usd()
        if cap <= 0:
            return None
        cost = request_max_cost_usd()
        with self._lock:
            try:
                spent = daily_cost_usd(ledger, now_fn=now_fn)
            except Exception:
                _log.warning(
                    "[budget_guard] 並行預留：每日成本帳本讀取失敗，fail-safe "
                    "拒絕本次預留（本輪 Bedrock 強制離線）",
                    exc_info=True,
                )
                return None
            # round(..., 6)：跟 `daily_cost_usd()` 同一套慣例，避免累加多筆
            # 浮點數（如 0.1+0.1+0.1 == 0.30000000000000004）在剛好卡在 cap
            # 邊界時被浮點誤差誤判超額、錯殺本該放行的請求。
            if round(spent + self._reserved + cost, 6) > cap:
                return None
            self._reserved = round(self._reserved + cost, 6)
            return cost

    def release(self, amount: float | None) -> None:
        """釋放先前 `try_reserve()` 成功回傳的預留額度（reconcile：pipeline
        跑完/失敗都要呼叫，讓其他 in-flight／後續請求能用回這段額度）。
        真實花費由 `ledger.append_run()`（`orchestrator.py` 收尾）另行記入
        帳本，這裡只解除「暫時佔位」，不影響、也不重複計費真實成本。
        `amount` 為 `None`（呼叫端根本沒拿到預留）時直接 no-op。
        """
        if amount is None:
            return
        with self._lock:
            self._reserved = round(max(0.0, self._reserved - amount), 6)


# 全域單例：process 內所有請求共用同一份「已預留」計數器。跟既有
# `web._rate_buckets` 之類 process-local 計數器同慣例，多實例部署不共享
# （見上方 docstring／模組頂部 follow-up 說明）。
_RESERVATION = BudgetReservation()


def try_reserve_request_budget(
    ledger: Ledger | None = None, *, now_fn=time.time
) -> float | None:
    """對外入口：`pipeline.run()` 在放行真 Bedrock（narrative 或
    online-stance）前呼叫，見 `BudgetReservation.try_reserve` docstring。"""
    return _RESERVATION.try_reserve(ledger, now_fn=now_fn)


def release_request_budget(amount: float | None) -> None:
    """對外入口：pipeline 完成或失敗後呼叫，釋放預留（見
    `BudgetReservation.release`）。"""
    _RESERVATION.release(amount)


def _reset_reservation_for_tests() -> None:
    """測試專用：把全域預留計數器歸零，避免測試之間互相污染（比照
    `conftest.py` 其他 process-local 狀態的隔離慣例）。非測試程式碼不應
    呼叫。"""
    with _RESERVATION._lock:
        _RESERVATION._reserved = 0.0


# ---------------------------------------------------------------------------
# codex HIGH 追加（unpriced model 破壞 cap）：model 計價檢查
# ---------------------------------------------------------------------------
# 背景：`request_max_cost_usd()` 是固定保守估值（預設 $0.05），估算依據是
# `ledger.PRICING` 裡「已知計價模型」（sonnet/haiku）的單價。但如果實際會
# 用到的 model（`BEDROCK_MODEL_ID` 或 stance 用的 `BEDROCK_HAIKU_MODEL_ID`）
# 換成一個**不在 `ledger.PRICING` 計價表裡**的模型，真實單價未知——固定
# $0.05 這個估值就不再是「保守上界」，可能嚴重低估真實花費，讓原本已修好
# 的並行預留機制形同虛設、$3/day cap 名存實亡。
#
# 修法（fail-closed）：放行真 Bedrock 前，narrative（`BEDROCK_MODEL_ID` env）
# 與 stance（`BEDROCK_HAIKU_MODEL_ID` env）**各自獨立**檢查
# 是否在計價表登記；只要即將真的被呼叫的那個 model 未計價，就不放行、強制
# 該段落離線 abstain（誠實 degrade，見 #24 不造假），而不是繼續用不可信的
# 固定估值硬撐。呼叫端見 `pipeline.run()`。
#
# follow-up（見對應 GitHub issue）：用 per-call 真實 worst-case token 用量
# 取代目前固定 $0.05 估值，是更大範圍的改動；現階段「固定保守 $0.05 +
# unpriced-fail-closed + $3/day cap」組合，對已知計價模型（sonnet/haiku）
# 已足夠安全，未計價模型則直接不放行，不會有低估風險。


def model_is_priced(model_id: str | None) -> bool:
    """`model_id` 是否在 `ledger.PRICING` 計價表中有明確定價。

    `None`／空字串／不在表中 → `False`（unpriced）：呼叫端必須 fail-closed
    ——不放行真 Bedrock、強制該段落離線 abstain，因為真實單價未知，現有的
    固定保守估值（`request_max_cost_usd()`）無法保證還是「上界」。
    """
    if not model_id:
        return False
    return model_id in PRICING


def narrative_model_priced() -> bool:
    """narrative 敘事呼叫這次實際會用到的 model（`BEDROCK_MODEL_ID` env）
    是否已在計價表登記。直接讀 env（而非透過 `BedrockConfig()`），確保
    每次呼叫都反映當下設定，見模組頂部 `_DEFAULT_STANCE_MODEL_ID` 註解。"""
    return model_is_priced(os.getenv("BEDROCK_MODEL_ID"))


def stance_model_priced() -> bool:
    """online-stance 分類呼叫這次實際會用到的 model（`BEDROCK_HAIKU_MODEL_ID`
    env，未設定時預設值本身已是計價表內的 haiku model）是否已在計價表登記。
    """
    model_id = os.getenv("BEDROCK_HAIKU_MODEL_ID") or _DEFAULT_STANCE_MODEL_ID
    return model_is_priced(model_id)


def warn_if_bedrock_model_unpriced() -> None:
    """啟動期檢查（供 `web.py` 模組載入時呼叫一次）：`BEDROCK_MODEL_ID`
    已設定但不在計價表 → 記警告 log，**不 crash**，實際 fail-closed 行為由
    `pipeline.run()` 每次請求各自呼叫 `narrative_model_priced()` 判斷、
    自動 degrade 離線（這裡只是讓維運及早在啟動 log 看到設定錯誤，不等到
    第一個公開請求才發現整天都在離線）。"""
    model_id = os.getenv("BEDROCK_MODEL_ID")
    if model_id and not model_is_priced(model_id):
        _log.warning(
            "[budget_guard] BEDROCK_MODEL_ID=%r 未在 ledger.PRICING 計價表登記，"
            "真實單價未知——narrative 呼叫將 fail-closed 強制離線 abstain，"
            "直到改回已計價的 model 或補上 PRICING 條目",
            model_id,
        )


def _config_bedrock_enabled_gate() -> bool:
    """config 層 `bedrock_enabled` 對 online-stance 側路的閘（PR-3 複審
    harper M1）：管理面「緊急關閉」（`bedrock_enabled=false`）必須統一蓋住
    **所有**真 Bedrock 路徑——`web._bedrock_allowed()` 只閘住 `?live=1`
    的 narrative 路徑，online-stance（`?real=1` 公開流量、只看 env 開關）
    若不讀 config，緊急關閉後 stance 分類呼叫仍會打真 Bedrock。

    語意與 `web._bedrock_allowed_resolved()` 的 config 分支一致：
    - 未設定過（item 不存在/欄位未設/壞欄，`bedrock_enabled is None`）→
      `True`（只看 env，v0.7.0 語意逐字相同）。
    - 明確 `False` → 擋；明確 `True` → 放行。
    - **讀取異常** → fail-closed `False`（無法確認管理面狀態就不放行真
      Bedrock 花費；分析照常走離線 stance，不炸）。方向跟 cap 的落 env
      不同是刻意的：cap 有「部署當下就存在的可信 env fallback」，開關的
      env 側（`BEDROCK_MODEL_ID`）已在呼叫端另行 AND 檢查，這裡管的是
      「管理面有沒有明確關閉」，讀不到就當作可能已關閉。

    走 `get_config_cached_failsoft()`（15s TTL + 15s 失敗負快取，qa M1）；
    ⚠️ 執行緒安全：呼叫端（`pipeline.run` 開頭、`web._online_stance_force_
    offline` 進限流鎖之前）都在任何 lock 之外呼叫，cache-miss 的 DynamoDB
    讀（有界 timeout）不會發生在計費/限流鎖內——與 cap 讀同一套紀律。"""
    try:
        cfg = admin_config.get_config_cached_failsoft()
    except Exception:
        _log.warning(
            "[budget_guard] admin config 讀取失敗，online-stance 閘 fail-closed"
            "（本輪 stance 維持離線；分析照常）",
            exc_info=True,
        )
        return False
    if cfg.bedrock_enabled is None:
        return True
    return cfg.bedrock_enabled


def online_stance_requested() -> bool:
    """online-stance 獨立總開關是否「已請求啟用」。

    需同時滿足：
    1. `TRUSTFORGE_ONLINE_STANCE` 為 truthy（`1`/`true`/`yes`/`on`，大小寫
       不拘）。
    2. `BEDROCK_MODEL_ID` 已設定真模型（非空字串）——沿用既有
       `web._bedrock_allowed()` env 分支判斷的同一個 env，避免只開 stance 開關卻沒
       設模型時誤以為已生效。
    3. config 層 `bedrock_enabled` 未明確關閉（PR-3 複審 harper M1，見
       `_config_bedrock_enabled_gate`）——管理面緊急關閉統一蓋住 online-
       stance 這條側路；config 未設定過則行為與 v0.7.0 逐字相同。

    兩個 env 預設皆未設 → 回 `False`，行為與加入本開關前逐字相同（stance
    呼叫在敘事離線時維持只讀持久化快取 + fail-safe "neutral"，不會無故打
    Bedrock）。env 檢查放前面短路：零設定離線 demo（env 皆未設）**完全
    不會**產生 DynamoDB config 讀，零 AWS 呼叫不變。
    """
    return (
        _env_flag("TRUSTFORGE_ONLINE_STANCE")
        and bool(os.getenv("BEDROCK_MODEL_ID"))
        and _config_bedrock_enabled_gate()
    )
