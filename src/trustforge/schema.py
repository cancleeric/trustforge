"""TrustForge 核心資料模型 — 對齊官方交付規格。

Evidence 欄位嚴格對應命題文件（source / fetched_at / content_reference / related_claim），
因主辦會抽查證據回溯性。Report 結構強制「事實 → 推論 → 結論」分層與三大必備章節。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

# 官方幣種池
COIN_POOL = ("BTC", "ETH", "SOL", "BNB", "XRP")


class QuestionType(str, Enum):
    MULTI_SOURCE = "multi_source"   # 多源整合
    HYPOTHESIS = "hypothesis"       # 假設驗證
    COMPARISON = "comparison"       # 比較分析


def iso_utc(ts: float) -> str:
    """epoch 秒 → ISO8601 UTC。ts<=0 視為未知，回空字串。"""
    if not ts or ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Evidence:
    """證據清單一筆。欄位對應官方規格，缺一不可回溯。"""
    source: str
    fetched_at: str          # ISO8601 UTC
    content_reference: str   # 引用片段 / 區間 / 數值 / 查詢條件
    related_claim: str
    source_url: str = ""
    kind: str = ""           # price/onchain/regulatory/hoyabit/news/social
    trust: float = 0.0
    trust_components: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BasisItem:
    """關鍵依據一條：判斷 + 解釋 + 對應的證據索引。"""
    claim: str
    explanation: str
    evidence_idx: list[int] = field(default_factory=list)


@dataclass
class Report:
    coin: str
    question_type: str
    question: str
    market_judgment: str            # 1. 結論 / 市場判斷
    facts: list[str]                # 事實層（客觀、高信任）
    inferences: list[str]           # 推論層（Agent 推理）
    key_basis: list[BasisItem]      # 2. 關鍵依據（對應 Evidence）
    confidence: float               # 0–1
    limits: list[str]               # 3. 信心說明：已知限制 / 資料不足
    could_flip: list[str]           # 可能推翻結論的條件
    contrarian: list[str]           # 反方 / 低信任證據
    generated_at: str

    def confidence_label(self) -> str:
        c = self.confidence
        return "高" if c >= 0.7 else "中" if c >= 0.45 else "低"

    def to_markdown(self, evidence: list[Evidence]) -> str:
        L: list[str] = []
        L.append(f"# {self.coin} 市場分析報告")
        L.append(f"> 題型：{self.question_type}｜生成時間：{self.generated_at}")
        L.append(f"> 問題：{self.question}\n")

        L.append("## 1. 結論 / 市場判斷")
        L.append(self.market_judgment or "（待 Agent 生成）")
        L.append(f"\n**整體信心：{self.confidence_label()}（{self.confidence:.2f}）**\n")

        L.append("## 2. 關鍵依據（事實 → 推論 → 結論）")
        L.append("### 事實（客觀資料）")
        for f in self.facts:
            L.append(f"- {f}")
        L.append("\n### 推論（Agent 推理）")
        for inf in self.inferences:
            L.append(f"- {inf}")
        L.append("\n### 依據對應證據")
        for b in self.key_basis:
            tags = "".join(f"[E{i}]" for i in b.evidence_idx)
            L.append(f"- **{b.claim}** {tags}\n  - {b.explanation}")

        L.append("\n## 3. 信心說明")
        L.append(f"信心程度：**{self.confidence_label()}**（{self.confidence:.2f}）")
        if self.limits:
            L.append("\n已知限制 / 資料不足：")
            for x in self.limits:
                L.append(f"- {x}")
        if self.could_flip:
            L.append("\n可能推翻結論的條件：")
            for x in self.could_flip:
                L.append(f"- {x}")

        if self.contrarian:
            L.append("\n## 反方 / 低信任證據（已標記，未納入主結論）")
            for x in self.contrarian:
                L.append(f"- {x}")

        L.append("\n## 證據清單對照")
        L.append("| # | source | fetched_at | trust | content_reference |")
        L.append("|---|--------|-----------|-------|-------------------|")
        for i, e in enumerate(evidence):
            ref = e.content_reference.replace("|", "\\|")[:80]
            L.append(f"| E{i} | {e.source} | {e.fetched_at} | {e.trust:.2f} | {ref} |")
        return "\n".join(L)
