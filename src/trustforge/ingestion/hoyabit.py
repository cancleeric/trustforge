"""HOYA BIT 連接器 interface / stub（issue #154，待 7/13 企業數據工作坊 spec）。

⚠️ 這是**佔位 stub**，不是真實連接器。7/13 工作坊給出 HOYA BIT 官方 API
spec（OHLCV / orderbook / trades 端點）後，才接真實呼叫。在 spec 到位前：

  - 預設 `enabled=False`：**不啟用**。避免誤把佔位 sample 資料當成真實高
    權威（hoyabit kind 聲譽 0.85，見 `trust/scoring.KIND_REPUTATION`）污染
    信任計算。只有管理端明確呼叫
    `base.set_source_enabled_override("hoyabit-ticker", True)`（或經
    admin_config 的 disabled_sources 反向排除）才會納入。
  - `fetch()` 回的佔位 Document **必定標 `meta["sample"]=True`** 且
    `meta["stub"]=True`——任何下游消費者都應把 `meta["sample"]=True` 視為
    「非真實資料、不可當權威」。codex 對抗審重點：stub 絕不發出任何真實
    外部 HTTP 請求（disabled 時零副作用；啟用時也只回佔位、不偷打 API）。
  - `get_depth()/get_orderbook()/get_trades()` 這些真實 API 方法**尚未實作**
    ，暫回 `NotImplementedError`（spec 到位才補真實呼叫，且必須走
    `safe_fetch` 的 SSRF-safe fetch，沿用各連接器既有慣例）。

social（Reddit）部分：**已確認不接**（Reddit 2025-11 終止 self-service，
見 milestone 收斂指示）。本模組與整條管線都不含任何 social/Reddit 真實或
stub 連接器——`hoyabit` 是唯一的「待接真實 API」佔位，專指交易所一手行情。
"""
from __future__ import annotations

import logging

from .base import Document, Source, get_source_enabled

_log = logging.getLogger(__name__)


class HoyaBitSource(Source):
    """HOYA BIT 交易所連接器 stub（issue #154，待 7/13 spec 接真實）。

    預設 disabled（不接真實 API 前不啟用）。`fetch()` 回標 sample 的佔位
    Document，**絕不發出任何真實外部 HTTP 請求**（codex 對抗審重點：stub
    不會誤發真實外部請求、disabled 時零副作用、啟用時也只回佔位不偷打 API）。
    """

    kind = "hoyabit"
    name = "hoyabit-ticker"
    # 預設 disabled：與通行 fail-closed（真實源預設 ON，見 #155）互補——這裡是
    # 「未接真實 API 前絕不啟用」，避免佔位資料被當真實高權威。只有管理端
    # 明確啟用才納入。
    enabled = False

    def __init__(self) -> None:
        super().__init__()
        self.last_attempts: int = 0
        self.last_failures: int = 0
        self.last_degraded: bool = False
        # 預設不啟用（見 class 屬性說明）；用 get_source_enabled 取當下真值，
        # 與 collect 過濾邏輯單一真值來源一致。
        self.enabled: bool = get_source_enabled(self.name)

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        """stub 佔位：回 1 筆標 sample 的 Document，**不**打任何真實 API。

        ⚠️ 這不是真實資料——`meta["sample"]=True` 必須被下游視為「非權威」。
        待 7/13 spec 到位，這個方法會改成打 HOYA BIT 真實端點、回真實
        Document（當時 `meta["sample"]` 不應出現，且必須走 `safe_fetch`
        的 SSRF-safe fetch）。
        """
        self.last_attempts += 1
        is_enabled = get_source_enabled(self.name)
        _log.info(
            "HOYA BIT stub fetch 被呼叫（coin=%r, query=%r）：尚未接真實 API，"
            "回佔位 sample Document，不發出任何外部請求",
            coin or "", query or "",
        )
        meta = {
            # 關鍵安全標記：這筆是佔位 sample，下游絕不可當真實高權威
            "sample": True,
            "stub": True,
            "disabled": not is_enabled,
            "content_reference": "HOYA BIT connector stub（未接真實 API）",
        }
        return [Document(
            id="hoyabit-stub-placeholder",
            kind=self.kind,
            source=self.name,
            text="HOYA BIT connector stub — 未接真實 API，此為佔位 sample 資料",
            url="",
            ts=0.0,
            meta=meta,
        )]

    def get_depth(self, coin: str) -> list[Document]:
        """待 7/13 spec：HOYA BIT orderbook depth 端點。尚未實作。"""
        raise NotImplementedError("HOYA BIT get_depth 尚未實作（待 7/13 spec）")

    def get_orderbook(self, coin: str) -> list[Document]:
        """待 7/13 spec：HOYA BIT orderbook 端點。尚未實作。"""
        raise NotImplementedError("HOYA BIT get_orderbook 尚未實作（待 7/13 spec）")

    def get_trades(self, coin: str) -> list[Document]:
        """待 7/13 spec：HOYA BIT trades 端點。尚未實作。"""
        raise NotImplementedError("HOYA BIT get_trades 尚未實作（待 7/13 spec）")


def build_hoyabit_sources() -> list[Source]:
    """回傳 HOYA BIT 連接器（預設 disabled stub）。註冊進 base.collect() 線上
    分支，但需明確 enabled 才納入（default disabled，見 HoyaBitSource）。"""
    return [HoyaBitSource()]
