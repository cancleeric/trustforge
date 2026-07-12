"""Phase 1 獨特洞察層 — 非顯而易見、可驗證的信任洞察。

世界 #1 定位 = 唯一誠實且可驗證的信任答案。本模組實作「別人只做表面的
非顯而易見洞察」，與既有 cross_source_signal（客觀 vs 情緒背離）互補，
但聚焦更深、更可被抽查的維度：

  - D1.1 聰明錢背離：鏈上累積/淨流入（以成交量趨勢作保守代理，平台尚無真實
         鏈上淨流入資料源）vs 價格下跌 = 聰明錢吸籌。
  - D1.2 操縱風險（同步滑動視窗爆量）：重啟停用中的 W3 burst 偵測，採多來源
         同步滑動視窗、取全域最大 ratio，稀疏來源誠實回「樣本不足」。
  - D1.4 來源自我矛盾：同一來源同時 bullish+bearish = 自我矛盾不確定性信號。

每條洞察都攜「兩個以上貢獻來源 + 方向 + 強度 + 資料覆蓋閘」——覆蓋不足
一律標「無法判定」，絕不硬湊（承接 Phase 0 三態誠實合約）。溯源鏈比情緒層
更深：點開可回溯原始 source / claim_id / 原文數值（見前端 InsightExplainabilityPanel）。

本模組刻意純函式、免 LLM、不重打任何連接器/Bedrock——只讀 `score()` 已算
好的 `ScoredClaim` 池與 `TrustedBrief`，是對既有結果的「重新解讀」，符合
本專案 $0 確定性、可審查的原則。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from ..ingestion.base import _matches_coin
from ..schema import QuestionType
from ..trust.scoring import ScoredClaim, TrustedBrief, _canonical_source

# 誠實覆蓋閘狀態值
COVERAGE_COVERED = "covered"
COVERAGE_INSUFFICIENT = "insufficient"


@dataclass
class InsightContribution:
    """洞察的一個貢獻來源（D1.3 可解釋性面板的最小單元）。

    - source / kind：原始來源與類型（供溯源回溯）。
    - claim_id：對應的 claim id，前端可點開回溯到 Evidence List / 原始句子。
    - direction：該貢獻提供的方向性信號（bullish/bearish/neutral）——
                 一條洞察由多個「方向相反」的貢獻對照而成（如價格跌 vs
                 成交量漲），方向欄讓使用者一眼看懂對立點在哪。
    - trust：該貢獻主張的信任分（0–1），供強度標註。
    """

    source: str
    kind: str
    claim_id: str | None
    text: str
    direction: str
    trust: float = 0.0


@dataclass
class Insight:
    """一條可驗證、非顯而易見的獨特洞察。

    coverage / coverage_reason 是誠實閘的核心：樣本不足時 coverage 標
    "insufficient"、summary 必須出現「無法判定」字樣，絕不補 0 或硬湊一個
    看似確定的結論（承接 Phase 0 三態誠實合約）。
    """

    insight_type: str
    title: str
    summary: str
    direction: str            # bullish / bearish / neutral / ambiguous
    strength: float           # 0–1 誠實強度（覆蓋不足時固定 0.0）
    coverage: str             # "covered" | "insufficient"
    coverage_reason: str
    contributions: list[InsightContribution]
    claim_ids: list[str]
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# D1.1 聰明錢背離（鏈上吸籌 vs 價格下跌）
# ---------------------------------------------------------------------------

# 價格事實由 ingestion/prices.py::price_facts 產生，doc.id 形如
# "price-{COIN}-ret" / "price-{COIN}-volume" / "price-{COIN}-vol"，來源
# source="ohlcv-csv"、kind="price"。本偵測器直接按 doc.id 前綴定位，不依賴
# 文字解析的脆弱性；文字只拿來抽數值與組摘要。
_RET_ID_SUFFIX = "-ret"
_VOL_ID_SUFFIX = "-volume"
_RET_RE = re.compile(r"報酬\s*([+-]?\d+(?:\.\d+)?)\s*%")
_VOL_RE = re.compile(r"變化\s*([+-]?\d+(?:\.\d+)?)\s*%")


def _extract_ret_pct(ret_claim: ScoredClaim) -> float | None:
    """從價格報酬事實抽取數值，最長優先讀結構化欄位。

    優先讀 `doc.meta["ret_pct"]`（由 ingestion/prices.price_facts 產生的結構化
    數值欄位），避免對句子正則的脆弱依賴；無結構化欄位時 fallback 到正則
    （向後相容未帶結構化欄位的舊價格事實）。兩者皆抽不到回 None（呼叫端應
    明確標註「無法解析」，禁止靜默回 None 假裝無背離）。"""
    meta_val = ret_claim.claim.doc.meta.get("ret_pct")
    if isinstance(meta_val, (int, float)) and not isinstance(meta_val, bool):
        return float(meta_val)
    m = _RET_RE.search(ret_claim.claim.text)
    return float(m.group(1)) if m else None


def _extract_vol_pct(vol_claim: ScoredClaim) -> float | None:
    """從成交量趨勢事實抽取數值，最長優先讀結構化欄位（見 `_extract_ret_pct`）。"""
    meta_val = vol_claim.claim.doc.meta.get("volume_trend_pct")
    if isinstance(meta_val, (int, float)) and not isinstance(meta_val, bool):
        return float(meta_val)
    m = _VOL_RE.search(vol_claim.claim.text)
    return float(m.group(1)) if m else None

# 誠實宣告：平台目前沒有真實的鏈上淨流入/累積資料源（onchain 連接器只提供
# FNG / 手續費 / 難度 / 區塊統計，不含交易所淨流入）。以「成交量趨勢」作為
# 鏈上吸籌的保守代理，必須在洞察裡明講，不能偽裝成「真實淨流入」。
_SMART_MONEY_PROXY_NOTE = (
    "本指標以成交量趨勢作為鏈上淨流入的保守代理；平台尚無真實鏈上淨流入"
    "資料源，僅供交叉驗證，不構成方向性建議。"
)


def _coin_price_claims(scored: Iterable[ScoredClaim], coin: str) -> list[ScoredClaim]:
    """篩出與該幣相關的價格事實主張（doc.id 前綴 `price-{COIN}-`）。"""
    coin_u = coin.upper()
    out: list[ScoredClaim] = []
    for sc in scored:
        doc = sc.claim.doc
        if doc.kind == "price" and doc.id.startswith(f"price-{coin_u}-"):
            out.append(sc)
    return out


def detect_smart_money_divergence(
    scored: Iterable[ScoredClaim], coin: str
) -> Insight | None:
    """D1.1：鏈上累積/淨流入（成交量趨勢代理）vs 價格下跌 = 聰明錢吸籌。

    誠實閘：
      - 缺價格報酬事實 → 不產生洞察（無資料不硬湊）。
      - 價格報酬數值無法解析（結構化欄位與正則皆未命中）→ 不靜默回 None，
        coverage="insufficient"，summary 標「無法判定」（issue #150：真實跌幅被
        正則漏抓時，靜默 None 會偽裝成「無背離」誤導使用者）。
      - 價格未下跌（上漲/盤整）→ 不成立，不產生洞察。
      - 價格下跌但成交量未上升/數值無法解析 → coverage="insufficient"，summary 標
        「無法判定」，強度 0.0（下跌過程成交量未增，不能確認是吸籌還是單純拋售）。
      - 價格下跌且成交量上升 → coverage="covered"，強度依跌幅與量增幅度保守計算。

    數值來源：優先讀 `doc.meta` 結構化欄位（ret_pct / volume_trend_pct，由
    ingestion/prices.price_facts 產生），避免對句子正則的脆弱依賴；無結構化欄位
    時 fallback 到正則（向後相容舊價格事實）。

    兩個貢獻來源（D1.3 對照）：價格報酬（bearish）+ 成交量趨勢（bullish），
    方向相反，正是「背離」的本質。
    """
    coin_u = coin.upper()
    claims = _coin_price_claims(scored, coin)
    ret_claim = next((sc for sc in claims if sc.claim.doc.id.endswith(_RET_ID_SUFFIX)), None)
    vol_claim = next((sc for sc in claims if sc.claim.doc.id.endswith(_VOL_ID_SUFFIX)), None)

    if ret_claim is None:
        # 無價格報酬事實：缺資料，誠實不出洞察（不假裝「無背離」）。
        return None

    # 讀結構化數值欄位（優先）或正則 fallback；兩者皆抽不到 → 明確標註無法解析，
    # 絕不靜默回 None（issue #150：真實跌幅若被正則漏抓，靜默 None 會偽裝成
    # 「無背離」，誤導使用者）。
    ret_val = _extract_ret_pct(ret_claim)
    if ret_val is None:
        return Insight(
            insight_type="smart_money_divergence",
            title="聰明錢背離（鏈上吸籌 vs 價格下跌）",
            summary=(
                f"{coin_u} 存在價格報酬事實，但報酬數值無法解析（結構化欄位與正則"
                "皆未命中），無法判定是否下跌及是否伴隨鏈上吸籌，故標註「無法判定」。"
            ),
            direction="ambiguous",
            strength=0.0,
            coverage=COVERAGE_INSUFFICIENT,
            coverage_reason="價格報酬數值無法解析，無法判斷方向。",
            contributions=[
                InsightContribution(
                    source=ret_claim.claim.doc.source,
                    kind=ret_claim.claim.doc.kind,
                    claim_id=ret_claim.claim.id,
                    text=ret_claim.claim.text,
                    direction="neutral",
                    trust=round(ret_claim.trust, 3),
                )
            ],
            claim_ids=[ret_claim.claim.id],
            meta={"proxy_note": _SMART_MONEY_PROXY_NOTE},
        )

    price_dir = "下跌" if ret_val < -1 else ("上漲" if ret_val > 1 else "盤整")

    if price_dir != "下跌":
        # 價格未下跌：聰明錢背離（吸籌於下跌中）不成立，不產生洞察。
        return None

    if vol_claim is None:
        return Insight(
            insight_type="smart_money_divergence",
            title="聰明錢背離（鏈上吸籌 vs 價格下跌）",
            summary=(
                f"{coin_u} 價格近區間下跌 {abs(ret_val):.1f}%，但缺少成交量趨勢訊號，"
                "無法判定是否伴隨鏈上吸籌（聰明錢），故標註「無法判定」。"
            ),
            direction="ambiguous",
            strength=0.0,
            coverage=COVERAGE_INSUFFICIENT,
            coverage_reason="缺少成交量趨勢事實，無法確認鏈上吸籌。",
            contributions=[
                InsightContribution(
                    source=ret_claim.claim.doc.source,
                    kind=ret_claim.claim.doc.kind,
                    claim_id=ret_claim.claim.id,
                    text=ret_claim.claim.text,
                    direction="bearish",
                    trust=round(ret_claim.trust, 3),
                )
            ],
            claim_ids=[ret_claim.claim.id],
            meta={"price_return_pct": ret_val, "proxy_note": _SMART_MONEY_PROXY_NOTE},
        )

    vol_val = _extract_vol_pct(vol_claim)
    if vol_val is None:
        # 成交量趨勢數值無法解析（結構化/正則皆失敗）→ 無法確認吸籌，誠實標註無法判定。
        return Insight(
            insight_type="smart_money_divergence",
            title="聰明錢背離（鏈上吸籌 vs 價格下跌）",
            summary=(
                f"{coin_u} 價格下跌 {abs(ret_val):.1f}%，但成交量趨勢數值無法解析"
                "（結構化欄位與正則皆未命中），無法確認鏈上吸籌（聰明錢），"
                "故標註「無法判定」。"
            ),
            direction="ambiguous",
            strength=0.0,
            coverage=COVERAGE_INSUFFICIENT,
            coverage_reason="成交量趨勢數值無法解析，無法確認吸籌。",
            contributions=[
                InsightContribution(
                    source=ret_claim.claim.doc.source,
                    kind=ret_claim.claim.doc.kind,
                    claim_id=ret_claim.claim.id,
                    text=ret_claim.claim.text,
                    direction="bearish",
                    trust=round(ret_claim.trust, 3),
                ),
                InsightContribution(
                    source=vol_claim.claim.doc.source,
                    kind=vol_claim.claim.doc.kind,
                    claim_id=vol_claim.claim.id,
                    text=vol_claim.claim.text,
                    direction="neutral",
                    trust=round(vol_claim.trust, 3),
                ),
            ],
            claim_ids=[ret_claim.claim.id, vol_claim.claim.id],
            meta={"price_return_pct": ret_val, "proxy_note": _SMART_MONEY_PROXY_NOTE},
        )

    if vol_val <= 0:
        # 價格跌但量未增：無法確認吸籌（可能是單純拋售），誠實標註無法判定。
        return Insight(
            insight_type="smart_money_divergence",
            title="聰明錢背離（鏈上吸籌 vs 價格下跌）",
            summary=(
                f"{coin_u} 價格下跌 {abs(ret_val):.1f}%，但成交量趨勢為 {vol_val:+.0f}%"
                "（未上升），無法確認鏈上吸籌（聰明錢），故標註「無法判定」。"
            ),
            direction="ambiguous",
            strength=0.0,
            coverage=COVERAGE_INSUFFICIENT,
            coverage_reason="價格下跌但成交量未上升，無法確認吸籌。",
            contributions=[
                InsightContribution(
                    source=ret_claim.claim.doc.source,
                    kind=ret_claim.claim.doc.kind,
                    claim_id=ret_claim.claim.id,
                    text=ret_claim.claim.text,
                    direction="bearish",
                    trust=round(ret_claim.trust, 3),
                ),
                InsightContribution(
                    source=vol_claim.claim.doc.source,
                    kind=vol_claim.claim.doc.kind,
                    claim_id=vol_claim.claim.id,
                    text=vol_claim.claim.text,
                    direction="neutral",
                    trust=round(vol_claim.trust, 3),
                ),
            ],
            claim_ids=[ret_claim.claim.id, vol_claim.claim.id],
            meta={"price_return_pct": ret_val, "volume_trend_pct": vol_val,
                  "proxy_note": _SMART_MONEY_PROXY_NOTE},
        )

    # 覆蓋充足：價格跌 + 量增 → 聰明錢背離（bullish divergence）。
    # 強度保守：跌幅（/30% 飽和）與量增幅（/50% 飽和）各半加權，clamp 在 [0.1, 1.0]。
    drop_mag = min(1.0, abs(ret_val) / 30.0)
    surge = min(1.0, vol_val / 50.0)
    strength = round(max(0.1, min(1.0, 0.45 * drop_mag + 0.55 * surge)), 3)

    return Insight(
        insight_type="smart_money_divergence",
        title="聰明錢背離（鏈上吸籌 vs 價格下跌）",
        summary=(
            f"{coin_u} 價格近區間下跌 {abs(ret_val):.1f}%，但成交量趨勢上升 {vol_val:+.0f}%"
            "（鏈上吸籌代理訊號），呈聰明錢背離——下跌過程中可能伴隨累積。"
            f"{_SMART_MONEY_PROXY_NOTE}"
        ),
        direction="bullish",
        strength=strength,
        coverage=COVERAGE_COVERED,
        coverage_reason="",
        contributions=[
            InsightContribution(
                source=ret_claim.claim.doc.source,
                kind=ret_claim.claim.doc.kind,
                claim_id=ret_claim.claim.id,
                text=ret_claim.claim.text,
                direction="bearish",
                trust=round(ret_claim.trust, 3),
            ),
            InsightContribution(
                source=vol_claim.claim.doc.source,
                kind=vol_claim.claim.doc.kind,
                claim_id=vol_claim.claim.id,
                text=vol_claim.claim.text,
                direction="bullish",
                trust=round(vol_claim.trust, 3),
            ),
        ],
        claim_ids=[ret_claim.claim.id, vol_claim.claim.id],
        meta={"price_return_pct": ret_val, "volume_trend_pct": vol_val,
              "proxy_note": _SMART_MONEY_PROXY_NOTE},
    )


# ---------------------------------------------------------------------------
# D1.2 操縱風險（同步滑動視窗爆量）— 重啟停用中的 W3 burst 偵測
# ---------------------------------------------------------------------------
# 歷史（見 trust.scoring._coordination_burst_flags / CTO 複查註解）：W3 burst 指標
# 因 4 輪 codex 對抗審持續挖出缺陷（中位數自含候選、固定牆鐘分桶、baseline 未對齊、
# **「只評估各源『最大窗』漏掉後續同窗 baseline 偏低的小爆量」**）被停用。本函式
# 採**多來源同步滑動視窗**修正方案重啟，專治最後一個未修缺陷：
#
#   不再只選「絕對數量最大」的單一視窗算 ratio——改為對每個來源在**每一個候選
#   視窗起點**都評估 ratio（同步對齊到其他來源的同窗計數當 baseline），取全域最大
#   ratio。這樣「絕對數量較小、但相對當下 baseline 更異常」的視窗才不會被漏掉。
#
# 誠實守則（呼應 #24 不虛增、不除零、不誤判）：
#   - 稀疏來源／主張數過少 → 同步視窗 baseline 不可靠 → 回 coverage="insufficient"
#     （summary 標「樣本不足，無法評估」），絕不硬湊一個看似確定的結論。
#   - 候選視窗絕對數量 < 下限（避免極小樣本的極端比值誤觸發）。
#   - 同窗其他來源中位數 <= 0 → 跳過該視窗（不除零、不讓「基準為 0」讓任何
#     >=1 則主張都被判爆量）。
#   - 本指標為**資訊型警示、不併入 trust 扣分**（與 W3 informational-only 定調
#     一致）：單源爆量可能是操縱，也可能是 legit 重大事件密集報導，需人工判讀。

_BURST_WINDOW_SEC = 3600.0        # 同步滑動視窗（60 分鐘）
_BURST_RATIO = 3.0                # 單源視窗內相異主張數 > 同窗其餘來源中位數的幾倍才觸發
_BURST_MIN_ABS_COUNT = 4          # 候選視窗最小絕對相異主張數（防極小樣本極端比值）
_BURST_MIN_TOTAL_CLAIMS = 6       # 整池最小主張數（稀疏閘）
_BURST_MIN_SOURCES = 2            # 至少需 2 個來源才能比較


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def _distinct_in_window(ordered: list[ScoredClaim], start_ts: float, end_ts: float) -> int:
    """數出 `doc.ts ∈ [start_ts, end_ts)` 區間內的相異 `c.claim.text` 數。"""
    return len({c.claim.text for c in ordered if start_ts <= c.claim.doc.ts < end_ts})


def detect_manipulation_burst(scored: Iterable[ScoredClaim]) -> Insight | None:
    """D1.2：多來源同步滑動視窗單源爆量偵測（修正版，重啟 W3 burst）。

    回傳：
      - 觸發 → coverage="covered" 的操縱風險洞察（資訊型，不扣分）。
      - 樣本過於稀疏（來源 < 2 或主張總數 < 下限）→ coverage="insufficient"、
        summary 標「樣本不足，無法評估」，強度 0（誠實不出假陰/假陽）。
      - 樣本充足但無爆量 → None（誠實不出洞察，不污染面板）。
    """
    claims_by_source: dict[str, list[ScoredClaim]] = {}
    for sc in scored:
        claims_by_source.setdefault(sc.claim.doc.source, []).append(sc)

    sources = list(claims_by_source)
    total = sum(len(v) for v in claims_by_source.values())

    if len(sources) < _BURST_MIN_SOURCES or total < _BURST_MIN_TOTAL_CLAIMS:
        return Insight(
            insight_type="manipulation_burst",
            title="操縱風險：單源爆量（同步滑動視窗）",
            summary=(
                "來源數或主張數過少，同步滑動視窗的跨源 baseline 不可靠，"
                "無法評估單源爆量，故標註「樣本不足，無法評估」。"
            ),
            direction="neutral",
            strength=0.0,
            coverage=COVERAGE_INSUFFICIENT,
            coverage_reason="來源/主張數過少，同步滑動視窗基準不可靠。",
            contributions=[],
            claim_ids=[],
            meta={},
        )

    ordered_by_source = {
        s: sorted(v, key=lambda c: (c.claim.doc.ts, c.claim.id))
        for s, v in claims_by_source.items()
    }

    best_ratio = 0.0
    best: tuple | None = None  # (source, start_ts, cnt, median, window_claims)

    for s, ordered in ordered_by_source.items():
        n = len(ordered)
        for i in range(n):
            start_ts = ordered[i].claim.doc.ts
            end_ts = start_ts + _BURST_WINDOW_SEC
            cnt = _distinct_in_window(ordered, start_ts, end_ts)
            # 最小絕對數量閘：避免極小樣本的極端比值誤觸發。
            if cnt < _BURST_MIN_ABS_COUNT:
                continue
            # 同步對齊：其他每個來源在「同一段時間窗」內各自發了幾則相異主張。
            others = [
                float(_distinct_in_window(o, start_ts, end_ts))
                for os_, o in ordered_by_source.items()
                if os_ != s
            ]
            if not others:
                continue
            median = _median(others)
            # 不除零、不讓「基準為 0」讓任何 >=1 則主張都被判爆量。
            if median <= 0:
                continue
            ratio = cnt / median
            # 取「全域最大 ratio」的視窗（含絕對數量較小但相對更異常的視窗）。
            if ratio > _BURST_RATIO and ratio > best_ratio:
                best_ratio = ratio
                window_claims = [c for c in ordered if start_ts <= c.claim.doc.ts < end_ts]
                best = (s, start_ts, cnt, median, window_claims)

    if best is None:
        return None

    s, _start_ts, cnt, median, window_claims = best
    strength = round(min(1.0, best_ratio / (2 * _BURST_RATIO)), 3)
    win_min = int(_BURST_WINDOW_SEC // 60)
    burst_trust = round(max((c.trust for c in window_claims), default=0.0), 3)
    burst_contrib = InsightContribution(
        source=s,
        kind=window_claims[0].claim.doc.kind,
        claim_id=window_claims[0].claim.id,
        text=f"來源 {s} 在 {win_min} 分鐘同步視窗內發出 {cnt} 則相異主張",
        direction="neutral",
        trust=burst_trust,
    )
    baseline_contrib = InsightContribution(
        source="(其餘來源)",
        kind="aggregated",
        claim_id=None,
        text=f"同窗對照其餘來源中位數 {median:g} 則（比值 {best_ratio:.1f}×，閾值 {_BURST_RATIO:.0f}×）",
        direction="neutral",
        trust=0.0,
    )
    return Insight(
        insight_type="manipulation_burst",
        title="操縱風險：單源爆量（同步滑動視窗）",
        summary=(
            f"來源 {s} 在 {win_min} 分鐘同步視窗內發出 {cnt} 則相異主張，同窗其餘來源"
            f"中位數僅 {median:g} 則（比值 {best_ratio:.1f}×，超過 {_BURST_RATIO:.0f}× 閾值）。"
            "此為資訊型警示（不併入信任扣分），需結合其他訊號人工判讀是否為協同操縱。"
        ),
        direction="neutral",
        strength=strength,
        coverage=COVERAGE_COVERED,
        coverage_reason="",
        contributions=[burst_contrib, baseline_contrib],
        claim_ids=[c.claim.id for c in window_claims][:10],
        meta={"ratio": round(best_ratio, 3), "cnt": cnt, "median": median,
              "window_sec": _BURST_WINDOW_SEC},
    )


# ---------------------------------------------------------------------------
# D1.4 來源自我矛盾（不確定性信號）— #21 / #72
# ---------------------------------------------------------------------------
# 同一來源同時出現 bullish + bearish 主張 → 該來源當下訊號「自我矛盾」，屬
# 不確定性信號（不是已判定的操縱，也不是背離，而是「這個來源自己左右互搏、
# 不可獨立採信」）。前端以「自我矛盾」徽章呈現（見 InsightExplainabilityPanel）。
#
# issue #72 收口：同源判斷用 `_canonical_source` 正規化（大小寫/空白變體收斂
# 成同一 key），避免 `"CoinDesk"` / `" coindesk "` 被誤判成兩個不同來源而漏掉
# 自我矛盾。僅看「明確方向」的主張（neutral 不計），且信任需過基本門檻避免
# 純雜訊觸發。
_SELF_CONTRA_MIN_TRUST = 0.35


def detect_source_self_contradiction(scored: Iterable[ScoredClaim]) -> list[Insight]:
    """D1.4：掃描每個（正規化後的）來源，若同時含 bullish 與 bearish 主張，
    產出一條「來源自我矛盾」不確定性洞察。可能多個來源各自矛盾 → 每源一條。"""
    by_src: dict[str, list[ScoredClaim]] = {}
    for sc in scored:
        if sc.claim.direction not in ("bullish", "bearish"):
            continue
        if sc.trust < _SELF_CONTRA_MIN_TRUST:
            continue
        by_src.setdefault(_canonical_source(sc.claim.doc.source), []).append(sc)

    out: list[Insight] = []
    for _src_key, claims in by_src.items():
        bull = [c for c in claims if c.claim.direction == "bullish"]
        bear = [c for c in claims if c.claim.direction == "bearish"]
        if not bull or not bear:
            continue
        b0, r0 = bull[0], bear[0]
        strength = round(min(1.0, 0.4 + 0.1 * (len(bull) + len(bear))), 3)
        contributions = [
            InsightContribution(
                source=b0.claim.doc.source, kind=b0.claim.doc.kind,
                claim_id=b0.claim.id, text=b0.claim.text,
                direction="bullish", trust=round(b0.trust, 3),
            ),
            InsightContribution(
                source=r0.claim.doc.source, kind=r0.claim.doc.kind,
                claim_id=r0.claim.id, text=r0.claim.text,
                direction="bearish", trust=round(r0.trust, 3),
            ),
        ]
        claim_ids = [c.claim.id for c in (bull + bear)]
        out.append(Insight(
            insight_type="source_self_contradiction",
            title="來源自我矛盾（不確定性信號）",
            summary=(
                f"來源 {b0.claim.doc.source} 同時出現看多與看空主張"
                f"（{len(bull)} 則偏多 / {len(bear)} 則偏空），構成自我矛盾——"
                "該來源當下訊號不可獨立採信，建議以其他獨立來源交叉驗證。"
            ),
            direction="ambiguous",
            strength=strength,
            coverage=COVERAGE_COVERED,
            coverage_reason="",
            contributions=contributions,
            claim_ids=claim_ids,
            meta={"source": b0.claim.doc.source, "n_bullish": len(bull),
                  "n_bearish": len(bear)},
        ))
    return out


# ---------------------------------------------------------------------------
# 洞察聚合入口
# ---------------------------------------------------------------------------

def detect_insights(
    brief: TrustedBrief,
    scored: Iterable[ScoredClaim],
    coin: str,
    qtype: QuestionType | str = QuestionType.MULTI_SOURCE,
) -> list[Insight]:
    """Phase 1 洞察聚合：掃描所有啟用的洞察偵測器，回傳本輪偵測到的洞察清單。

    純函式、免 LLM、不重打連接器：只讀 `score()` 已算好的 `ScoredClaim` 池
    與 `TrustedBrief`。每個偵測器各自負責自己的誠實閘（覆蓋不足回 None 或
    coverage="insufficient"）。

    `scored` 應為**完整、未截斷**的主張全集（與 `build_report` 的 `scored`
    參數同源），內部依 `_matches_coin` 過濾出本幣相關主張，不受
    `aggregate()` 的 supporting/contrarian 截斷影響。
    """
    coin_relevant = [sc for sc in scored if _matches_coin(sc.claim.doc, coin)]
    insights: list[Insight] = []

    sm = detect_smart_money_divergence(coin_relevant, coin)
    if sm is not None:
        insights.append(sm)

    burst = detect_manipulation_burst(coin_relevant)
    if burst is not None:
        insights.append(burst)

    # D1.4 來源自我矛盾：可能多個來源各自矛盾，逐條附加。
    insights.extend(detect_source_self_contradiction(coin_relevant))

    return insights
