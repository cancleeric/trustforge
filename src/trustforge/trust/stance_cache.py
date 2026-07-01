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

# 不可見的分隔符，避免原句剛好含常見符號（如 "|"）造成 key 碰撞。
_KEY_SEP = "␟"


def normalize(text: str) -> str:
    """正規化：小寫 + 摺疊空白。保留原句文字，不做 tokenize/停用詞處理。"""
    return " ".join(text.strip().lower().split())


def cache_key(a: str, b: str) -> str:
    """順序無關 key：正規化後排序組合，讓 (a, b) 與 (b, a) 命中同一筆快取。"""
    na, nb = normalize(a), normalize(b)
    first, second = sorted((na, nb))
    return f"{first}{_KEY_SEP}{second}"


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
    client, cache: StanceCache | None = None
) -> Callable[[str, str], str]:
    """把 `client.classify_stance(a, b)` 包一層快取，回傳可直接傳給
    `trust.scoring._corroboration(..., stance_fn=...)` 的純函式。

    cache 未提供時，預設用 `DEFAULT_CACHE_PATH`（demo/sample_data/stance_cache.json）
    建立快取（含持久化層讀取）。
    """
    if cache is None:
        cache = StanceCache(DEFAULT_CACHE_PATH)

    def _fn(a: str, b: str) -> str:
        cached = cache.get(a, b)
        if cached is not None:
            return cached
        label = client.classify_stance(a, b)
        if label not in _VALID_LABELS:
            label = "neutral"
        cache.set(a, b, label)
        return label

    return _fn
