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
  news.py:      coindesk, decrypt, cryptopanic, cointelegraph, bitcoinmagazine,
                cryptoslate, bitcoinist, newsbtc, dailyhodl, theblock, utoday,
                blockworks
  onchain.py:   alternative-me-fng（coin-agnostic）, blockchain-info,
                mempool-space-fees, mempool-space-difficulty, blockchair
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
    shared_pool: str | None = None    # 與其他 source 共用同一組配額額度的 pool key（見下方
                                       # `SHARED_POOL_LABEL`）；None = 獨立配額（或無配額）。
                                       # ⛔ codex HIGH（#24、PR #41）：3 個 coingecko-* source
                                       # 共用同一組 Demo API key 額度，若逐 source 各自算「配額
                                       # 使用%」會嚴重低估真實使用率（如 3 源各 40% 但實際共用
                                       # key 已 120% 超額，逐 source % 會把超額隱藏起來）——因此
                                       # 用 `shared_pool` 標記同一額度池的成員，供 `web.py` 呈現
                                       # 時把它們合併成一行、加總呼叫數，不逐 source 假裝獨立。


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

# 共用配額池 key → 顯示用中文標籤。目前只有 CoinGecko 3 個 source 共用一組
# Demo API key 額度；`web.py::_render_connector_usage_table()` 據此把同池成員
# 合併成一行呈現（呼叫數加總），不逐 source 各自顯示會嚴重誤導的獨立「配額%」。
_COINGECKO_SHARED_POOL = "coingecko-demo-key"
SHARED_POOL_LABEL: dict[str, str] = {
    _COINGECKO_SHARED_POOL: "CoinGecko（price + sentiment + dev，共用 demo key）",
}


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
        shared_pool=_COINGECKO_SHARED_POOL,
    ),
    "coingecko-sentiment": ConnectorCostModel(
        source="coingecko-sentiment", free_tier_quota=_COINGECKO_FREE_QUOTA,
        free_tier_period="month", free_tier_reference=_COINGECKO_REF,
        paid_unit_cost_usd=_COINGECKO_PAID_UNIT_COST, paid_tier_note=_COINGECKO_PAID_NOTE,
        shared_pool=_COINGECKO_SHARED_POOL,
    ),
    "coingecko-dev": ConnectorCostModel(
        source="coingecko-dev", free_tier_quota=_COINGECKO_FREE_QUOTA,
        free_tier_period="month", free_tier_reference=_COINGECKO_REF,
        paid_unit_cost_usd=_COINGECKO_PAID_UNIT_COST, paid_tier_note=_COINGECKO_PAID_NOTE,
        shared_pool=_COINGECKO_SHARED_POOL,
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
    # 資料密度第一批（#24，docs/PLAN-data-density.md）新增 6 家新聞 RSS，
    # 全部 keyless 公開 RSS feed，比照 coindesk/decrypt 同款登記方式。
    "cointelegraph": _free_connector(
        "cointelegraph", "公開 RSS feed（https://cointelegraph.com/rss），無官方公開量化硬配額",
    ),
    "bitcoinmagazine": _free_connector(
        "bitcoinmagazine", "公開 RSS feed（https://bitcoinmagazine.com/feed），無官方公開量化硬配額",
    ),
    "cryptoslate": _free_connector(
        "cryptoslate", "公開 RSS feed（https://cryptoslate.com/feed/），無官方公開量化硬配額",
    ),
    "bitcoinist": _free_connector(
        "bitcoinist", "公開 RSS feed（https://bitcoinist.com/feed/），無官方公開量化硬配額",
    ),
    "newsbtc": _free_connector(
        "newsbtc", "公開 RSS feed（https://www.newsbtc.com/feed/），無官方公開量化硬配額",
    ),
    "dailyhodl": _free_connector(
        "dailyhodl", "公開 RSS feed（https://dailyhodl.com/feed/），無官方公開量化硬配額",
    ),
    # 資料密度第二批（#24，docs/PLAN-data-density.md）新增 The Block/U.Today/
    # Blockworks 3 家新聞 RSS，全部 keyless 公開 RSS feed，比照第一批同款登記。
    "theblock": _free_connector(
        "theblock", "公開 RSS feed（https://www.theblock.co/rss.xml），無官方公開量化硬配額",
    ),
    "utoday": _free_connector(
        "utoday", "公開 RSS feed（https://u.today/rss.php），無官方公開量化硬配額",
    ),
    "blockworks": _free_connector(
        "blockworks", "公開 Atom feed（https://blockworks.com/feed），無官方公開量化硬配額",
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
    # 資料密度第二批：mempool.space keyless 公開 API，官方文件無嚴格月配額
    # 公告（僅建議 <10 req/min，非配額制）；Blockchair 免費層有明確公開配額
    # （1440 req/day），但屬「日配額」非本模型 `free_tier_period` 支援的
    # "month" 語意（沿用 CoinGecko 那組月配額語意會誤導），比照其餘公開端點
    # 誠實標「無官方公開量化硬配額」（`free_tier_quota=None`），配額數字改寫
    # 在 `free_tier_reference` 純文字說明供人工覆核，不臆測成月配額換算。
    "mempool-space-fees": _free_connector(
        "mempool-space-fees",
        "mempool.space 公開 API（https://mempool.space/api/v1/fees/recommended），"
        "官方文件無嚴格 QPS 硬配額（建議 <10 req/min），無付費方案",
        paid_note="無付費方案",
    ),
    "mempool-space-difficulty": _free_connector(
        "mempool-space-difficulty",
        "mempool.space 公開 API（https://mempool.space/api/v1/difficulty-adjustment），"
        "官方文件無嚴格 QPS 硬配額（建議 <10 req/min），無付費方案",
        paid_note="無付費方案",
    ),
    "blockchair": _free_connector(
        "blockchair",
        "Blockchair 公開 API（https://api.blockchair.com/bitcoin/stats），免費層"
        "官方公告 1440 requests/day（日配額，非本模型月配額欄位語意，見"
        "https://blockchair.com/api/docs），無付費方案",
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


# ⛔ codex HIGH（#24、PR #41）：本模組刻意**不提供**「配額使用%」計算函式。
# `/status`「連接器用量」的呼叫數來源是 `scheduler_log.recent()`（rolling
# 最近 N 次排程執行 window），不是嚴格日曆月配額會計；若直接拿這個 rolling
# window 的呼叫數去除以官方「月配額」算百分比，會呈現一個看似精確、實則
# 語意錯誤的數字（rolling window ≠ calendar month），且對共用同一組 key
# 額度的 source（見 `shared_pool`）逐 source 分別算 % 還會嚴重低估真實使用率
# （3 源各 40% 但共用 key 實際已超額 120%）。若未來要提供真的配額百分比，
# 應該先做月曆月（calendar month）bucket 計數，而不是在 rolling window 上
# 硬套百分比公式——那是本 PR 範圍外的較大工程，這裡先誠實只顯示原始呼叫數
# +官方配額參考文字，不顯示百分比。
