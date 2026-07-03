"""真實鏈上資料連接器 (P0-2)。

來源白名單（寫死，防 SSRF）：
  - Alternative.me Fear & Greed Index  https://api.alternative.me/fng/?limit=7  (完全免費)
  - Blockchain.info stats              https://api.blockchain.info/stats         (公開，BTC only)

資料密度第二批（#24，2026-07，見 docs/PLAN-data-density.md，gray 已逐一 curl
驗證 200 OK）——全部 keyless、僅 BTC（比照 `BlockchainInfoSource` 非 BTC
跳過的既有慣例，非全市場通用信號，不列入 `COIN_AGNOSTIC_SOURCES`）：
  - mempool.space 建議手續費   https://mempool.space/api/v1/fees/recommended
  - mempool.space 難度調整進度 https://mempool.space/api/v1/difficulty-adjustment
  - Blockchair BTC 鏈上統計    https://api.blockchair.com/bitcoin/stats
    （免費層 1440 req/day，官方文件：https://blockchair.com/api/docs）

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/
    hostname/port/私有 IP，DNS pinning 杜絕 rebinding，禁自動跟轉
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# codex MEDIUM（PR #55，第 5 輪，CEO 決策：有界新鮮度窗，per-source、無
# scheduler 大改；跨快取單調性另列 follow-up，見 BlockchairStatsSource
# 內的說明）：`best_block_time` 語法正確不代表新鮮——供應商可能重放/快取
# 命中一份陳舊的舊回應。窗口寬度取保守值，正常出塊節奏下永遠滿足：
_FUTURE_SKEW_TOLERANCE_SECONDS = 10 * 60       # 未來容差 10 分鐘（時鐘飄移）
_MAX_STALENESS_SECONDS = 6 * 60 * 60           # 過舊上限 6 小時（BTC 出塊
                                                # 均值 ~10 分鐘，慢時可到
                                                # 1-2 小時，抓保守 6 小時）


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 safe_fetch.py）。"""
    return safe_fetch.fetch_url(url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES)


# ---------------------------------------------------------------------------
# 資料密度第二批 codex HIGH 修復（#24 + robustness，PR #55）：mempool.space /
# Blockchair 3 個新源原本用 `dict.get(key, "N/A")` 補位，任何限流回應／
# 供應商錯誤 envelope／schema drift（缺欄位、欄位型別跑掉成字串）都會被
# 靜靜組成一筆「帶當前時間戳的 N/A 假證據」發布出去，排程器視為成功、
# **覆蓋掉還能用的舊快取**、也不會被失敗監控抓到。
#
# 修法：每個必要欄位都用 `_require_number()` 嚴格驗證「存在 + 是有限數值
# （非 None／字串／bool／NaN／inf）」，任何一項不合格就 `raise ValueError`
# ——**絕不**用 "N/A"／空 dict 補位發布。`fetch()` 拋例外後，呼叫端
# （`scripts/fetch_scheduler.py` 每個來源都包 `try/except Exception`）會
# 保留舊快取、計入 `failures`，不會覆蓋、也會被監控看到（同其餘來源既有
# 的失敗語意，見 `test_*_source_failure_does_not_crash_collect` 系列）。
# ---------------------------------------------------------------------------

def _require_number(
    data: dict,
    key: str,
    context: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    exclusive_min: bool = False,
) -> float:
    """驗證 `data[key]` 存在且為有限數值（`bool` 雖是 `int` 子類但不算數值，
    避免 JSON 的 `true`/`false` 被誤當成 1/0 接受），否則 `raise ValueError`
    ——不得回傳 "N/A" 或任何佔位字串讓呼叫端誤當真資料發布。

    codex MEDIUM（PR #55，第 8 輪，鏈上驗證最終閉合）：型別合法不代表語意
    合理——負手續費、負區塊數、進度超出 0–100%、sentinel `-1` 等「型別對
    但語意不可能」的數值，過去都會被 `_require_number` 放行、包成一筆
    帶新鮮時間戳的 Document 發布，覆蓋掉還能用的舊快取。加上 `min_value`/
    `max_value`（含邊界）與 `exclusive_min`（區塊高度/難度這類「必須嚴格
    > 0」的欄位）選用範圍檢查，違反一律 raise，不建 Document。
    """
    if key not in data:
        raise ValueError(f"{context}: 回應缺少必要欄位 {key!r}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}: 欄位 {key!r} 型別錯誤（非數值）：{value!r}")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise ValueError(f"{context}: 欄位 {key!r} 不是有限數值：{value!r}")
    if min_value is not None:
        if exclusive_min:
            if value <= min_value:
                raise ValueError(
                    f"{context}: 欄位 {key!r} 數值語意不合理（須 > {min_value!r}）：{value!r}"
                )
        elif value < min_value:
            raise ValueError(
                f"{context}: 欄位 {key!r} 數值語意不合理（須 >= {min_value!r}）：{value!r}"
            )
    if max_value is not None and value > max_value:
        raise ValueError(
            f"{context}: 欄位 {key!r} 數值語意不合理（須 <= {max_value!r}）：{value!r}"
        )
    return value


def _fmt_num(value: float) -> str:
    """把驗證過的數值格式化成顯示文字：整數值不印多餘的 `.0`。"""
    if value == int(value):
        return str(int(value))
    return str(value)


class FearGreedSource(Source):
    """Alternative.me 恐懼貪婪指數（完全免費，所有幣種通用）。"""
    kind = "onchain"
    name = "alternative-me-fng"
    _URL = "https://api.alternative.me/fng/?limit=7"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        docs: list[Document] = []
        for entry in data.get("data", []):
            value = entry.get("value", "")
            classification = entry.get("value_classification", "")
            ts_str = entry.get("timestamp", "")
            try:
                ts = float(ts_str)
            except (ValueError, TypeError):
                ts = 0.0
            if ts > 0:
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            else:
                date_str = ""
            ref = f"Fear & Greed Index: {value} ({classification}), {date_str}"
            doc_id = "onchain-fng-" + hashlib.md5(ref.encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind="onchain",
                source=self.name,
                text=ref,
                url=self._URL,
                ts=ts,
                meta={"content_reference": ref},
            ))
        return docs


class BlockchainInfoSource(Source):
    """Blockchain.info BTC 全網統計（公開，免 key，僅適用 BTC）。"""
    kind = "onchain"
    name = "blockchain-info"
    _URL = "https://api.blockchain.info/stats"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # 非 BTC 幣種跳過
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        # blockchain.info/stats returns timestamp in **milliseconds**; divide by 1000
        ts_raw = float(data.get("timestamp", datetime.now(tz=timezone.utc).timestamp() * 1000))
        ts = ts_raw / 1000
        market_price = data.get("market_price_usd", "N/A")
        hash_rate = data.get("hash_rate", "N/A")
        ref = f"BTC market_price_usd={market_price}, hash_rate={hash_rate}"
        doc_id = "onchain-binfo-" + hashlib.md5(ref.encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=f"BTC 市場價格：{market_price} USD，算力：{hash_rate} GH/s",
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


class MempoolSpaceFeesSource(Source):
    """mempool.space BTC 建議手續費（keyless，公開，僅適用 BTC）。"""
    kind = "onchain"
    name = "mempool-space-fees"
    _URL = "https://mempool.space/api/v1/fees/recommended"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # 非 BTC 幣種跳過（同 BlockchainInfoSource 慣例，非全市場通用信號）
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{self.name}: 回應不是 JSON object：{type(data).__name__}")
        # 嚴格驗證（codex HIGH，#24+robustness，PR #55）：任一欄位缺失/型別
        # 錯誤（限流、供應商錯誤 envelope、schema drift）一律拋例外，絕不用
        # "N/A" 補位發布假證據——呼叫端（fetch_scheduler.py）會保留舊快取。
        # codex MEDIUM 第 8 輪：手續費（sat/vB）語意上不可能為負，sentinel
        # -1 或限流異常回應都可能出現負值，須擋下。
        fastest = _require_number(data, "fastestFee", self.name, min_value=0)
        half_hour = _require_number(data, "halfHourFee", self.name, min_value=0)
        hour = _require_number(data, "hourFee", self.name, min_value=0)
        economy = _require_number(data, "economyFee", self.name, min_value=0)
        minimum = _require_number(data, "minimumFee", self.name, min_value=0)
        ref = (
            f"BTC 建議手續費（sat/vB）：最快={_fmt_num(fastest)}，"
            f"30分鐘={_fmt_num(half_hour)}，1小時={_fmt_num(hour)}，"
            f"經濟={_fmt_num(economy)}，最低={_fmt_num(minimum)}"
        )
        ts = time.time()
        doc_id = "onchain-mpfee-" + hashlib.md5(f"{ref}-{int(ts)}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


class MempoolSpaceDifficultySource(Source):
    """mempool.space BTC 難度調整進度（keyless，公開，僅適用 BTC）。"""
    kind = "onchain"
    name = "mempool-space-difficulty"
    _URL = "https://mempool.space/api/v1/difficulty-adjustment"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{self.name}: 回應不是 JSON object：{type(data).__name__}")
        # 嚴格驗證（同 MempoolSpaceFeesSource，見模組頂部說明）：欄位缺失/
        # 型別錯誤一律拋例外，不接受 "N/A" 佔位。
        # codex MEDIUM 第 8 輪：進度百分比語意上只能在 0–100 之間；剩餘
        # 區塊數不可能為負；難度變化率本就可正可負（下修合法），不限範圍。
        progress = _require_number(data, "progressPercent", self.name, min_value=0, max_value=100)
        change = _require_number(data, "difficultyChange", self.name)
        remaining = _require_number(data, "remainingBlocks", self.name, min_value=0)
        progress_str = f"{progress:.1f}%"
        change_str = f"{change:+.2f}%"
        ref = f"BTC 難度調整進度：{progress_str}，預估變化 {change_str}，剩餘 {_fmt_num(remaining)} 區塊"
        ts = time.time()
        doc_id = "onchain-mpdiff-" + hashlib.md5(f"{ref}-{int(ts)}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


class BlockchairStatsSource(Source):
    """Blockchair BTC 鏈上統計（公開，免費層 1440 req/day，僅適用 BTC）。"""
    kind = "onchain"
    name = "blockchair"
    _URL = "https://api.blockchair.com/bitcoin/stats"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"{self.name}: 回應不是 JSON object：{type(payload).__name__}")
        # 先驗 context.code（Blockchair 錯誤 envelope／限流/欠費會用非 200
        # 的 code 回應，`data` 屆時可能缺失或為 null——必須先擋在這裡，
        # 不能讓下面的欄位驗證用空 dict 補位假裝正常，見模組頂部說明）。
        context = payload.get("context")
        code = context.get("code") if isinstance(context, dict) else None
        if code != 200:
            error_msg = context.get("error") if isinstance(context, dict) else None
            raise ValueError(
                f"{self.name}: API 回應非成功狀態（context.code={code!r}, error={error_msg!r}）"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"{self.name}: 回應缺少有效 data object：{type(data).__name__}")
        # 嚴格驗證（同 MempoolSpace* 兩源，見模組頂部說明）：必要統計欄位
        # 缺失/型別錯誤一律拋例外，不接受 "N/A" 佔位。
        # codex MEDIUM 第 8 輪：區塊高度/難度語意上必須嚴格 > 0（0 或負值
        # 是 sentinel／不可能的鏈上狀態）；mempool 交易數/24h 交易數不可能
        # 為負，但 0 是合法狀態（mempool 剛好清空）。
        blocks = _require_number(data, "blocks", self.name, min_value=0, exclusive_min=True)
        difficulty = _require_number(data, "difficulty", self.name, min_value=0, exclusive_min=True)
        mempool_tx = _require_number(data, "mempool_transactions", self.name, min_value=0)
        tx_24h = _require_number(data, "transactions_24h", self.name, min_value=0)
        # codex MEDIUM（PR #55，鏈上驗證最終閉合）：`best_block_time` 也視為
        # 必要欄位——缺失/null/格式漂移正是「payload 不完整/schema drift」
        # 的訊號，跟其他必要欄位一致一律 raise，不得退回 `time.time()` 把
        # 歷史或不完整的回應偽裝成「剛取得」的新證據去覆蓋舊快取（違反本
        # 批「驗證失敗不建 Document、保留舊快取」的核心不變量）。
        best_block_time = data.get("best_block_time")
        if not isinstance(best_block_time, str) or not best_block_time:
            raise ValueError(
                f"{self.name}: 欄位 'best_block_time' 缺失或型別錯誤：{best_block_time!r}"
            )
        ref = (
            f"BTC 鏈上統計：區塊高度={_fmt_num(blocks)}，難度={_fmt_num(difficulty)}，"
            f"mempool 交易數={_fmt_num(mempool_tx)}，24h 交易數={_fmt_num(tx_24h)}"
        )
        try:
            ts = (
                datetime.strptime(best_block_time, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except ValueError as exc:
            raise ValueError(
                f"{self.name}: 欄位 'best_block_time' 格式不符預期：{best_block_time!r}"
            ) from exc
        # codex MEDIUM（PR #55，第 5 輪，CEO 決策：有界新鮮度窗）：格式正確
        # 不代表新鮮——供應商可能重放/命中一份陳舊快取。用有界窗擋掉明顯
        # bogus（未來時間戳）與陳舊重放（超過合理出塊節奏的過舊時間戳），
        # 窗內視為正常新鮮資料。
        #
        # follow-up（非本輪）：跨快取單調性（新 doc ts 不得早於上一輪已快取
        # doc 的 ts）是 scheduler 層的通用增強、該套用到所有來源而不只
        # blockchair，需要獨立設計，已建 issue 追蹤，見
        # docs/OPTIMIZATION-PLAN-weakness.md 的 follow-up 段落。
        now = time.time()
        if ts > now + _FUTURE_SKEW_TOLERANCE_SECONDS:
            raise ValueError(
                f"{self.name}: 欄位 'best_block_time' 是未來時間戳（明顯 bogus）："
                f"{best_block_time!r}（ts={ts}, now={now}）"
            )
        if ts < now - _MAX_STALENESS_SECONDS:
            raise ValueError(
                f"{self.name}: 欄位 'best_block_time' 過舊（可能是重放的陳舊 payload）："
                f"{best_block_time!r}（ts={ts}, now={now}）"
            )
        doc_id = "onchain-bcair-" + hashlib.md5(ref.encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


def build_onchain_sources() -> list[Source]:
    """回傳所有已啟用的鏈上連接器。"""
    return [
        FearGreedSource(),
        BlockchainInfoSource(),
        MempoolSpaceFeesSource(),
        MempoolSpaceDifficultySource(),
        BlockchairStatsSource(),
    ]
