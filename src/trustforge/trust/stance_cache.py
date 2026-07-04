"""W1.5（#15）語意 stance 分類快取。

兩層快取：
1. 記憶體 dict：單次執行（一次 score() 呼叫）內有效，避免同一批 claims 互相佐證
   時對同一對主張重複呼叫 Bedrock（見 scoring._corroboration：A vs B、B vs A 會各
   命中一次，key 需順序無關才能互相命中）。
2. 持久化 JSON：離線讀取 `demo/sample_data/stance_cache.json`，讓沒有 AWS 憑證的
   環境也能重放先前跑過的 stance 判斷結果（不打真 AWS，本 PR 不寫回這個檔案，
   寫入/生成快取是 CEO 另立的受控步驟）。

key 正規化：小寫 + 去除多餘空白（保留原句，不 tokenize），排序後組合，讓
(a, b) 與 (b, a) 命中同一筆快取。

value schema：{"label": "entailment"|"contradiction"|"neutral", "version": STANCE_CACHE_VERSION}
version 不符即視為 miss（prompt/system prompt 或模型版本變更時，只要在此檔手動
bump STANCE_CACHE_VERSION，舊快取就自動失效，不需要手動清檔案）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..ingestion.base import SAMPLE_DIR

# prompt/model 版本號。改動 bedrock._STANCE_SYSTEM / _STANCE_FEWSHOT 或
# BedrockConfig.stance_model_id 的語意（非單純換一個效果相同的模型）時要 bump 這裡。
STANCE_CACHE_VERSION = "v1"

_VALID_LABELS = frozenset({"entailment", "contradiction", "neutral"})

DEFAULT_CACHE_PATH = SAMPLE_DIR / "stance_cache.json"


def normalize(text: str) -> str:
    """正規化：小寫 + 摺疊空白。保留原句文字，不做 tokenize/停用詞處理。"""
    return " ".join(text.strip().lower().split())


def cache_key(a: str, b: str) -> str:
    """順序無關 key：正規化後排序組合，讓 (a, b) 與 (b, a) 命中同一筆快取。

    安全性（CEO+codex 對抗審發現）：`a`/`b` 是使用者可控文字（claim 原文），
    不可用裸分隔符字串接（即使選罕見字元）——只要輸入恰好包含該分隔符，就能構造
    `("a<SEP>b", "c")` 與 `("a", "b<SEP>c")` 這種不同語意輸入卻算出同一把 key
    的碰撞，讓惡意 claim 挪用別對主張的快取結果（壓制合法佐證或繞過矛盾閘）。
    改用 canonical JSON 陣列序列化：json.dumps 對字串內容做正確跳脫（含任何分隔符
    候選字元本身），陣列邊界由跳脫過的引號界定，不會因為內容剛好像分隔符就混淆。
    """
    na, nb = normalize(a), normalize(b)
    pair = sorted((na, nb))
    return json.dumps(pair, ensure_ascii=False, separators=(",", ":"))


class StanceCache:
    """兩層 stance 分類快取：記憶體（本次執行）+ 持久化 JSON（離線重放）。"""

    def __init__(self, persistent_path: str | Path | None = None):
        self._mem: dict[str, dict] = {}
        self._persistent: dict[str, dict] = {}
        if persistent_path is not None:
            self.load(persistent_path)

    def load(self, path: str | Path) -> None:
        """讀取持久化 JSON。檔案不存在或格式錯誤時靜默略過，不拋錯（離線 demo 容錯）。"""
        p = Path(path)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._persistent.update(data)

    def get(self, a: str, b: str) -> str | None:
        """查快取；miss（含 version 不符 / label 非法）回 None。"""
        key = cache_key(a, b)
        entry = self._mem.get(key)
        if entry is None:
            entry = self._persistent.get(key)
        if not isinstance(entry, dict):
            return None
        if entry.get("version") != STANCE_CACHE_VERSION:
            return None
        label = entry.get("label")
        return label if label in _VALID_LABELS else None

    def set(self, a: str, b: str, label: str) -> None:
        """寫入記憶體層（本次執行內有效）。不寫回持久化 JSON。"""
        if label not in _VALID_LABELS:
            return
        key = cache_key(a, b)
        self._mem[key] = {"label": label, "version": STANCE_CACHE_VERSION}


def cached_stance_fn(
    client=None, cache: StanceCache | None = None, budget=None
) -> Callable[[str, str], str]:
    """把 `client.classify_stance(a, b)` 包一層快取，回傳可直接傳給
    `trust.scoring._corroboration(..., stance_fn=...)` 的純函式。

    cache 未提供時，預設用 `DEFAULT_CACHE_PATH`（demo/sample_data/stance_cache.json）
    建立快取（含持久化層讀取），讓離線環境也能重放先前算好的 stance 判斷。

    `client` 可為 None（CEO+codex 對抗審發現的修復）：離線模式或沒有可用的線上
    client 時，呼叫端應仍能建立這個 stance_fn 以便讀取持久化快取——若因此排除
    `client is None` 就整個不建 stance_fn，離線 pipeline 會永遠讀不到
    `stance_cache.json` 的預算結果，#15 的修復在公開離線 demo 路徑上完全看不到。

    `budget`：選用的呼叫預算物件（duck-typed，只需要 `.take() -> bool`，通常是
    `trust.scoring._StanceBudget`）。第 3 輪對抗審修正：預算只能限制**真正的
    Bedrock 呼叫**（cost/time），不該限制免費的 cache-hit——若在查快取「之前」
    就先扣預算，大量 cache-hit（離線 demo 的常態）會把全域預算耗盡，導致後面
    真正的 cache-miss（可能含快取裡的真 contradiction）也被跳過檢查、錯判為
    佐證，等於能被排序操縱繞過矛盾閘。所以 `budget.take()` 只在**確認 cache
    miss、即將發起真呼叫**的這一刻才呼叫；cache-hit（含命中 contradiction）
    永遠先於預算檢查、直接回傳，不消耗、不受預算耗盡與否影響。

    cache miss 時：
    - `client` 為 None，或 `client.offline` 為真（沒有可用的線上模型）→ fail-safe
      直接回 "neutral"，不呼叫 `client.classify_stance`（不 raise、不打真 AWS），
      也**不寫入快取**（避免把「沒查到」誤存成 neutral 的既定事實，之後接上真線上
      client 或補上持久化快取時應該能重新判斷，而不是被這次的 fail-safe 值卡住）。
    - 有可用 client，但 `budget.take()` 回 False（額度或時間耗盡）→ 同樣 fail-safe
      回 "neutral"，不呼叫、不寫入快取。
    - 否則才呼叫 `client.classify_stance(a, b)` 並把結果寫入快取。
    """
    if cache is None:
        cache = StanceCache(DEFAULT_CACHE_PATH)

    def _fn(a: str, b: str) -> str:
        cached = cache.get(a, b)
        if cached is not None:
            return cached  # cache-hit：免費，不消耗預算
        if client is None:
            return "neutral"
        # #9 online-stance 預算配額硬化：優先看 `stance_offline`（若存在），
        # 讓 `pipeline.run()` 能在敘事離線時仍讓 stance 判斷改用真 Bedrock
        # （見 `bedrock.BedrockClient.__init__` docstring）。duck-typed 測試
        # client 未必有這個屬性時，回退到既有的 `offline`（向後相容，逐字
        # 沿用加入這個屬性前的行為）。
        stance_offline = getattr(client, "stance_offline", None)
        if stance_offline is None:
            stance_offline = getattr(client, "offline", False)
        if stance_offline:
            return "neutral"
        if budget is not None and not budget.take():
            return "neutral"  # 只有「確認需要真呼叫」的這一刻才可能因預算耗盡降級
        label = client.classify_stance(a, b)
        if label not in _VALID_LABELS:
            label = "neutral"
        cache.set(a, b, label)
        return label

    return _fn
