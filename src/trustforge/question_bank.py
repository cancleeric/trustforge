"""Competition prompt bank derived from the official three question classes.

The bank intentionally contains original prompts.  Public financial QA datasets
can inform coverage ideas, but copied questions neither prove system capability
nor guarantee that a live competition prompt is in scope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Iterator

from .schema import COIN_POOL, QuestionType


@dataclass(frozen=True)
class QuestionCase:
    id: str
    question_type: QuestionType
    query: str
    coin: str | None = None
    coin_a: str | None = None
    coin_b: str | None = None
    coverage_tags: tuple[str, ...] = ()
    origin: str = "TrustForge original prompt; official category expansion"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["question_type"] = self.question_type.value
        data["coverage_tags"] = list(self.coverage_tags)
        return data


_MULTI_SOURCE = (
    ("整合近兩週價格、成交量、鏈上、新聞與社群訊號，整理市場判斷與限制。", ("price", "onchain", "news", "social")),
    ("說明價格變動是否有鏈上流量或交易所資金流佐證；若沒有，清楚標示證據缺口。", ("price", "onchain", "evidence_gap")),
    ("比對新聞與社群情緒是否一致，並指出來源時效與可能的操弄風險。", ("news", "social", "freshness", "manipulation")),
    ("檢查政府公告或監管文件是否改變近期風險背景，列出可回溯來源。", ("government", "regulatory", "provenance")),
    ("以多來源資料判斷目前是趨勢延續、反轉，或資訊不足；不要給交易指令。", ("price", "onchain", "news", "abstention")),
    ("找出相互衝突的來源訊號，分別說明事實、推論與需要補證的地方。", ("conflict", "claim_traceability")),
    ("評估近期波動、回撤與流動性訊號，並連結到五年 OHLCV 基準與分析視窗。", ("price", "five_year_lineage", "risk")),
    ("分析爬蟲資料的新鮮度與缺失是否足以影響結論，列出可能推翻結論的條件。", ("crawler", "freshness", "limits")),
    ("整理正面與負面消息各自的可信度、來源數量與交叉佐證情況。", ("news", "corroboration", "trust")),
    ("針對異常成交或資金流說明資料來源、執行時間與可重現方式。", ("onchain", "execution_log", "reproducibility")),
    ("以報告格式呈現市場狀態、關鍵證據、完整度、限制及反轉條件。", ("report_contract", "evidence")),
    ("從官方資料、爬蟲來源與市場資料中找出尚未驗證的主張，避免過度推論。", ("government", "crawler", "abstention")),
)

_HYPOTHESES = (
    ("近期上漲具有多來源支撐", ("price", "onchain", "news")),
    ("近期下跌主要由監管風險觸發", ("government", "regulatory", "news")),
    ("社群熱度正在領先基本面或鏈上活動", ("social", "onchain", "divergence")),
    ("交易所資金流顯示短期賣壓正在增加", ("onchain", "risk")),
    ("波動擴大但尚無足夠資訊判斷方向", ("price", "abstention", "limits")),
    ("正面新聞已被多個獨立來源交叉佐證", ("news", "corroboration", "trust")),
    ("政府公告對市場情緒造成可觀察的改變", ("government", "social", "news")),
    ("近期資料與五年歷史風險區間顯著不同", ("five_year_lineage", "price", "risk")),
    ("爬蟲延遲或快取過期足以降低結論可信度", ("crawler", "freshness", "execution_log")),
    ("來源間矛盾表示結論應保持中性或保留", ("conflict", "abstention")),
    ("成交量變化與價格方向一致且不是單一來源假象", ("price", "corroboration")),
    ("監管來源缺漏會實質改變目前的風險判讀", ("government", "evidence_gap", "could_flip")),
)

_COMPARISONS = (
    ("比較近期價格、波動與回撤，指出風險差異。", ("price", "risk")),
    ("比較鏈上與交易所資金流，指出資料不完整之處。", ("onchain", "evidence_gap")),
    ("比較正負新聞與社群情緒的交叉佐證程度。", ("news", "social", "corroboration")),
    ("比較政府公告與監管風險的相關證據，不給投資建議。", ("government", "regulatory", "abstention")),
    ("比較五年 OHLCV 歷史與近期分析視窗中的風險狀態。", ("five_year_lineage", "price", "risk")),
    ("比較來源新鮮度、爬蟲快取和執行時間可能造成的偏差。", ("crawler", "freshness", "execution_log")),
    ("比較證據覆蓋率與來源衝突，決定哪一方更需要保留結論。", ("evidence", "conflict", "abstention")),
    ("比較市場敘事是否受到單一社群或媒體來源主導。", ("social", "news", "manipulation")),
    ("比較監管文件、新聞與價格是否出現一致或背離訊號。", ("government", "news", "price")),
    ("比較資料缺口對兩者市場判斷的影響與可推翻條件。", ("limits", "could_flip", "evidence_gap")),
    ("比較短期訊號與長期歷史背景，區分事實與推論。", ("five_year_lineage", "claim_traceability")),
    ("以完整競賽報告欄位比較兩者：結論、證據、完整度、限制與執行 log。", ("report_contract", "evidence", "execution_log")),
)


def iter_cases() -> Iterator[QuestionCase]:
    """Yield 240 deterministic cases: 60 multi-source, 60 hypothesis, 120 comparison."""
    for coin in COIN_POOL:
        for index, (prompt, tags) in enumerate(_MULTI_SOURCE, start=1):
            yield QuestionCase(
                id=f"ms-{coin.lower()}-{index:02d}", question_type=QuestionType.MULTI_SOURCE,
                coin=coin, query=f"請分析 {coin}：{prompt}", coverage_tags=tags,
            )
        for index, (hypothesis, tags) in enumerate(_HYPOTHESES, start=1):
            yield QuestionCase(
                id=f"hy-{coin.lower()}-{index:02d}", question_type=QuestionType.HYPOTHESIS,
                coin=coin, query=f"驗證假設：「{coin} {hypothesis}」。請同時蒐集支持與反對證據。", coverage_tags=tags,
            )
    for coin_a, coin_b in combinations(COIN_POOL, 2):
        for index, (prompt, tags) in enumerate(_COMPARISONS, start=1):
            yield QuestionCase(
                id=f"cp-{coin_a.lower()}-{coin_b.lower()}-{index:02d}", question_type=QuestionType.COMPARISON,
                coin_a=coin_a, coin_b=coin_b,
                query=f"請比較 {coin_a} 與 {coin_b}：{prompt}", coverage_tags=tags,
            )


def all_cases() -> list[QuestionCase]:
    return list(iter_cases())
