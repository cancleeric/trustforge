"""共用分析管線 — CLI 與 web 服務都呼叫這裡，確保兩條路徑行為一致。

走顯式 3 步驟 agent 推理（run_agent_pipeline）：
  Step1 Claim 抽取(Bedrock/regex fallback) → Step2 信任評分聚合 → Step3 帶溯源行文 → Step4 限制複審。
"""
from __future__ import annotations

from .agent.orchestrator import run_agent_pipeline
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import collect
from .schema import Evidence, QuestionType, Report


def run(coin: str, query: str, qtype: QuestionType,
        offline: bool = False, data_dir=None) -> tuple[Report, list[Evidence], ExecutionLog]:
    """跑完整管線：collect → 多步 agent 推理 → 報告。回傳 (report, evidence, log)。"""
    coin = coin.upper()
    log = ExecutionLog()
    log.record("ingestion.collect", params={"coin": coin, "offline": offline})
    docs = collect(query, coin=coin, offline=offline, data_dir=data_dir)
    if not docs:
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    report, evidence = run_agent_pipeline(
        query, coin, qtype, docs,
        client=BedrockClient(offline=offline), log=log,
    )
    return report, evidence, log
