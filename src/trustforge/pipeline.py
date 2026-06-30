"""共用分析管線 — CLI 與 web 服務都呼叫這裡，確保兩條路徑行為一致。"""
from __future__ import annotations

import time

from .agent.orchestrator import build_report
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import collect
from .schema import Evidence, QuestionType, Report
from .trust.scoring import aggregate, extract_claims, score


def run(coin: str, query: str, qtype: QuestionType,
        offline: bool = False, data_dir=None) -> tuple[Report, list[Evidence], ExecutionLog]:
    """跑完整管線：collect → 信任評分 → 聚合 → 報告。回傳 (report, evidence, log)。"""
    coin = coin.upper()
    log = ExecutionLog()
    log.record("ingestion.collect", params={"coin": coin, "offline": offline})
    docs = collect(query, coin=coin, offline=offline, data_dir=data_dir)
    if not docs:
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    # 離線樣本視為最新（以最新文件為時效基準）；線上用當下時間
    now = max((d.ts for d in docs), default=time.time()) if offline else time.time()

    claims = extract_claims(docs)
    scored = score(claims, now=now)
    log.record("trust.score", summary=f"claims={len(claims)}")
    brief = aggregate(scored, query)
    log.record("trust.aggregate", summary=f"supporting={len(brief.supporting)} confidence={brief.confidence:.2f}")

    report, evidence = build_report(
        query, coin, qtype, brief,
        client=BedrockClient(offline=offline), log=log,
    )
    return report, evidence, log
