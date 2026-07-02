"""連接器成本模型（成本會計階段2）：硬編、可審計的常數，比照 `ledger.py::PRICING`
的作法——查證官方文件後寫死成 Python 常數，附官方連結註解供覆核，**不做任何
網路呼叫**（credit-safe：本檔案匯入零外部依賴、零 I/O）。

⛔ 誠實原則（CEO gray 計劃「每個成本都要算」+ #24 誠實標示要求）：
  - 官方文件有明確公開量化配額的（目前只有 CoinGecko Demo API），才填
    `free_tier_quota`；其餘公開端點（RSS/公開 API，非申請制、非配額制）一律
    誠實標「無官方硬配額」（`free_tier_quota=None`），不臆測數字。
  - `paid_unit_cost_usd` 是「若升級到付費方案」的**假設性**參考單價，本專案
    目前所有連接器都是免費層 keyless／公開端點，**沒有任何一個真的付費**。
    `estimate_connector_cost()` 預設一律回 `$0.0`（見 `is_paid_tier_enabled()`），
    只有明確用 env 開關啟用付費層時才會用這裡的假設單價計算——避免免費用量
    被誤顯示成「正在花錢」。

來源名稱（`source`）對應 `src/trustforge/ingestion/*.py` 各 `Source.name`：
  news.py:      coindesk, decrypt, cryptopanic
  onchain.py:   alternative-me-fng（coin-agnostic）, blockchain-info
  regulatory.py: sec-gov（coin-agnostic）
  social.py:    reddit-cryptocurrency, reddit-bitcoin
  coingecko.py: coingecko-price, coingecko-sentiment, coingecko-dev
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorCostModel:
    """單一連接器的成本模型常數。"""

    source: str
    free_tier_quota: int | None       # 官方文件明確公開的配額數字；None = 官方未公開量化硬配額
    free_tier_period: str             # "month" | "n/a"（quota 為 None 時填 "n/a"）
    free_tier_reference: str          # 官方文件連結／說明（純註解性質，人工查證，不做網路驗證）
    paid_unit_cost_usd: float | None  # 假設性每次呼叫成本（USD）；None = 無官方付費方案／未評估
    paid_tier_note: str               # 付費方案的假設依據說明（誠實標「假設」，未啟用）


# --- CoinGecko（coingecko.py：3 個 source 共用同一組免費 Demo API key 額度） ---
# 官方定價頁：https://www.coingecko.com/en/api/pricing
#   Demo Plan（免費，需申請 demo API key）：10,000 calls/month，30 calls/min。
#   quota 是整個 API key 共用，不是逐 endpoint 各自 10,000（三個 coingecko-*
#   source 共用同一把 key 額度，見 `ingestion/coingecko.py` 模組頂部說明）。
_COINGECKO_FREE_QUOTA = 10_000
_COINGECKO_REF = (
    "CoinGecko Demo API Plan：10,000 calls/month，30 calls/min "
    "（https://www.coingecko.com/en/api/pricing；3 個 coingecko-* source 共用同一組 key 額度）"
)
# Analyst 付費方案：$129/月、500,000 calls/月（同一份官方定價頁）。純粹換算單次
# 呼叫成本供「假設性」參考，本專案未申請、未啟用、未付費，不代表任何實際支出。
_COINGECKO_PAID_UNIT_COST = round(129.0 / 500_000, 8)
_COINGECKO_PAID_NOTE = (
    "假設：CoinGecko Analyst 方案 $129/月／500,000 calls（未啟用，僅供未來若升級時參考）"
)

_NO_PAID_TIER_NOTE = "無官方付費方案／未評估"


def _free_connector(source: str, reference: str, paid_note: str = _NO_PAID_TIER_NOTE) -> ConnectorCostModel:
    """公開端點（RSS／非申請制公開 API）、無官方公開量化配額的連接器共用建構器，
    避免 11 個 source 重複寫一樣的 `free_tier_quota=None, free_tier_period="n/a"`。"""
    return ConnectorCostModel(
        source=source, free_tier_quota=None, free_tier_period="n/a",
        free_tier_reference=reference, paid_unit_cost_usd=None, paid_tier_note=paid_note,
    )


CONNECTOR_COST_MODEL: dict[str, ConnectorCostModel] = {
    "coingecko-price": ConnectorCostModel(
        source="coingecko-price", free_tier_quota=_COINGECKO_FREE_QUOTA,
        free_tier_period="month", free_tier_reference=_COINGECKO_REF,
        paid_unit_cost_usd=_COINGECKO_PAID_UNIT_COST, paid_tier_note=_COINGECKO_PAID_NOTE,
    ),
    "coingecko-sentiment": ConnectorCostModel(
        source="coingecko-sentiment", free_tier_quota=_COINGECKO_FREE_QUOTA,
        free_tier_period="month", free_tier_reference=_COINGECKO_REF,
        paid_unit_cost_usd=_COINGECKO_PAID_UNIT_COST, paid_tier_note=_COINGECKO_PAID_NOTE,
    ),
    "coingecko-dev": ConnectorCostModel(
        source="coingecko-dev", free_tier_quota=_COINGECKO_FREE_QUOTA,
        free_tier_period="month", free_tier_reference=_COINGECKO_REF,
        paid_unit_cost_usd=_COINGECKO_PAID_UNIT_COST, paid_tier_note=_COINGECKO_PAID_NOTE,
    ),
    "coindesk": _free_connector(
        "coindesk",
        "公開 RSS feed（https://www.coindesk.com/arc/outboundfeeds/rss/），無官方公開量化硬配額",
    ),
    "decrypt": _free_connector(
        "decrypt", "公開 RSS feed（https://decrypt.co/feed），無官方公開量化硬配額",
    ),
    "cryptopanic": _free_connector(
        "cryptopanic",
        "公開端點，未申請／未使用付費 developer API key，無官方公開量化硬配額",
    ),
    "alternative-me-fng": _free_connector(
        "alternative-me-fng",
        "Alternative.me Fear & Greed Index 公開 API"
        "（https://alternative.me/crypto/fear-and-greed-index/），無官方公開量化硬配額",
        paid_note="無付費方案",
    ),
    "blockchain-info": _free_connector(
        "blockchain-info",
        "blockchain.info 公開 API（https://www.blockchain.com/explorer/api），無官方公開量化硬配額",
        paid_note="無付費方案",
    ),
    "sec-gov": _free_connector(
        "sec-gov",
        "SEC EDGAR 公開 Atom feed；SEC 僅公開「Fair Access」速率建議"
        "（建議 ≤10 requests/sec，非月配額，見 https://www.sec.gov/os/webmaster-faq#developers），"
        "無月配額",
        paid_note="無付費方案（政府公開資料）",
    ),
    "reddit-cryptocurrency": _free_connector(
        "reddit-cryptocurrency",
        "Reddit 公開 RSS／search feed（非 OAuth API），無官方公開量化硬配額",
    ),
    "reddit-bitcoin": _free_connector(
        "reddit-bitcoin",
        "Reddit 公開 RSS／search feed（非 OAuth API），無官方公開量化硬配額",
    ),
}


def is_paid_tier_enabled(source: str) -> bool:
    """`source` 是否實際啟用付費方案。預設一律 `False`——TrustForge 目前所有
    連接器都是免費層 keyless／公開端點，沒有任何一個真的付費。

    Gate 用 env `TRUSTFORGE_PAID_TIER_SOURCES`（逗號分隔的 source 名稱清單），
    純粹是「若未來真的升級付費方案」時的開關；credit-safe 開發/CI 環境下
    不應該、也不會被設定，`estimate_connector_cost()` 因此恆回 `$0.0`。"""
    enabled = os.getenv("TRUSTFORGE_PAID_TIER_SOURCES", "")
    names = {s.strip() for s in enabled.split(",") if s.strip()}
    return source in names


def estimate_connector_cost(source: str, call_count: int) -> float:
    """估算 `source` 在 `call_count` 次呼叫下的成本（USD）。

    Free tier（預設，未啟用付費方案）恆回 `0.0`——即使 `CONNECTOR_COST_MODEL`
    附了假設性付費單價，只要沒有用 `TRUSTFORGE_PAID_TIER_SOURCES` 明確啟用該
    source，就不能顯示成「正在花錢」，否則會誤導使用者以為免費用量有成本。
    只有明確啟用付費層、且該 source 在 `CONNECTOR_COST_MODEL` 有登記假設單價
    時，才用該單價估算。`call_count` 為負值（不應發生，防禦性 clamp）視為 0。"""
    if call_count <= 0 or not is_paid_tier_enabled(source):
        return 0.0
    model = CONNECTOR_COST_MODEL.get(source)
    if model is None or model.paid_unit_cost_usd is None:
        return 0.0
    return round(call_count * model.paid_unit_cost_usd, 6)


def quota_percent(source: str, call_count: int) -> float | None:
    """回傳 `source` 在 `call_count` 次呼叫下的免費額度使用百分比（可能 >100，
    代表已超額）。`source` 沒有登記、或該 source 官方未公開量化配額時回
    `None`——呼叫端（`web.py`）據此決定要不要顯示「配額%」欄（見階段2需求：
    「配額%(有配額才顯)」）。"""
    model = CONNECTOR_COST_MODEL.get(source)
    if model is None or not model.free_tier_quota:
        return None
    return round(call_count / model.free_tier_quota * 100, 2)
