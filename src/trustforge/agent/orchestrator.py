"""Agent 編排：信任加權 brief → 結構化 Report + Evidence List + Execution Log。

反作弊鐵則：**判斷結構、證據整合、信任評分由本 pipeline 產生**，
Bedrock 只負責把推理「行文」成可讀敘述，不得把第三方現成結論當主要結果。
"""
from __future__ import annotations

import time

from ..bedrock import BedrockClient
from ..execlog import ExecutionLog
from ..schema import BasisItem, Evidence, QuestionType, Report, iso_utc
from ..trust.scoring import ScoredClaim, TrustedBrief

OBJECTIVE_KINDS = {"price", "onchain", "regulatory", "hoyabit"}

SYSTEM = (
    "你是加密市場分析助理。只能依據提供的『已信任加權證據』作答，"
    "區分事實/推論/結論，標註信心與限制，不提供投資建議。"
    "你的任務是把證據行文成可讀推理，不得引入未提供的外部結論。"
)


def _scored_to_evidence(sc: ScoredClaim, related: str) -> Evidence:
    doc = sc.claim.doc
    ref = doc.meta.get("content_reference") or sc.claim.text[:120]
    return Evidence(
        source=doc.source,
        fetched_at=iso_utc(doc.ts),
        content_reference=ref,
        related_claim=related,
        source_url=doc.url,
        kind=doc.kind,
        trust=round(sc.trust, 3),
        trust_components={k: round(v, 3) for k, v in sc.components.items()},
    )


def _direction(brief: TrustedBrief) -> str:
    """從高信任價格事實判方向（我方判斷，非外部結論）。"""
    for sc in brief.supporting:
        if sc.claim.doc.kind == "price":
            t = sc.claim.text
            if "上漲" in t:
                return "偏多"
            if "下跌" in t:
                return "偏空"
            if "盤整" in t:
                return "中性"
    return "中性"


def _derive_limits(brief: TrustedBrief) -> tuple[list[str], list[str]]:
    limits: list[str] = []
    flips: list[str] = []
    kinds = {sc.claim.doc.kind for sc in brief.supporting}
    if len(kinds) < 3:
        limits.append(f"資料來源類型僅 {len(kinds)} 類（<3），多源整合度有限，結論不確定性較高。")
    if brief.confidence < 0.5:
        limits.append("整體信心偏低，支撐證據不足以形成強判斷。")
    if brief.contrarian:
        limits.append(f"存在 {len(brief.contrarian)} 條反方／低信任訊號，已標記但未納入主結論。")
        flips.append("若反方訊號獲得高信任獨立來源佐證，結論可能反轉。")
    flips.append("出現高信任的反向鏈上大額流動或監管事件時，須重評。")
    return limits, flips


def build_report(query: str, coin: str, qtype: QuestionType, brief: TrustedBrief,
                 client: BedrockClient | None = None,
                 log: ExecutionLog | None = None,
                 now_fn=time.time) -> tuple[Report, list[Evidence]]:
    client = client or BedrockClient(offline=True)
    log = log or ExecutionLog(now_fn=now_fn)

    # 1. 證據清單（支撐 + 反方）
    log.record("evidence.build", summary=f"supporting={len(brief.supporting)} contrarian={len(brief.contrarian)}")
    evidence: list[Evidence] = []
    key_basis: list[BasisItem] = []
    ev_index: dict[tuple, int] = {}   # (source, content_reference) → 去重,保留最高 trust
    judgment_tag = f"{coin} 市場判斷"

    def _add_evidence(sc: ScoredClaim, related: str) -> int:
        ev = _scored_to_evidence(sc, related)
        # key 含角色(related):支撐與反方即使同來源同引用也不共用 bucket,避免 silent drop
        key = (ev.source, ev.content_reference, related)
        if key in ev_index:
            idx = ev_index[key]
            if ev.trust > evidence[idx].trust:   # 同來源同引用 → 留最高信任那筆
                evidence[idx] = ev
            return idx
        idx = len(evidence)
        evidence.append(ev)
        ev_index[key] = idx
        return idx

    for sc in brief.supporting:
        idx = _add_evidence(sc, judgment_tag)
        key_basis.append(BasisItem(
            claim=sc.claim.text,
            explanation=f"來源 {sc.claim.doc.source}（{sc.claim.doc.kind}），信任 {sc.trust:.2f}。",
            evidence_idx=[idx],
        ))
    for sc in brief.contrarian:
        _add_evidence(sc, "反方／低信任訊號")

    # 2. 我方判斷（pipeline 產生，非外部結論）
    direction = _direction(brief)
    facts = [sc.claim.text for sc in brief.supporting if sc.claim.doc.kind in OBJECTIVE_KINDS]
    n_indep = len({sc.claim.doc.source for sc in brief.supporting})

    if qtype == QuestionType.HYPOTHESIS:
        head = f"針對假設「{query}」：依現有證據，{coin} 短期傾向{direction}。"
    elif qtype == QuestionType.COMPARISON:
        head = f"{coin} 當前市場位置：{direction}。（比較分析需對每個幣種各跑一次 pipeline 後並列）"
    else:
        head = f"{coin} 當前市場狀態判斷：{direction}。"
    market_judgment = head + f"（{n_indep} 個獨立來源支撐，整體信心 {brief.confidence:.2f}）"
    log.record("judgment.derive", params={"direction": direction, "indep_sources": n_indep})

    # 3. Bedrock 行文（離線為佔位；結構不依賴它）
    prompt = (
        f"幣種：{coin}\n題型：{qtype.value}\n問題：{query}\n"
        f"我方判斷：{market_judgment}\n事實：\n" + "\n".join(f"- {f}" for f in facts) +
        "\n請用 2-3 句把上述事實串成事實→推論→結論的推理，僅依事實，勿引入外部結論。"
    )
    narrative = client.complete(system=SYSTEM, prompt=prompt)
    log.record("bedrock.complete", params={"model": client.config.model_id or "offline"},
               summary="生成推論敘述")
    inferences = [
        f"客觀價格事實指向{direction}；由 {n_indep} 個獨立來源交叉佐證。",
        narrative.strip(),
    ]

    limits, flips = _derive_limits(brief)
    report = Report(
        coin=coin, question_type=qtype.value, question=query,
        market_judgment=market_judgment, facts=facts, inferences=inferences,
        key_basis=key_basis, confidence=brief.confidence,
        limits=limits, could_flip=flips,
        contrarian=[sc.claim.text for sc in brief.contrarian],
        generated_at=iso_utc(now_fn()),
    )
    log.record("report.done", summary=f"facts={len(facts)} basis={len(key_basis)} evidence={len(evidence)}")
    return report, evidence
