"""共用分析管線 — CLI 與 web 服務都呼叫這裡，確保兩條路徑行為一致。

走顯式 3 步驟 agent 推理（run_agent_pipeline）：
  Step1 Claim 抽取(Bedrock/regex fallback) → Step2 信任評分聚合 → Step3 帶溯源行文 → Step4 限制複審。
"""
from __future__ import annotations

from .agent.orchestrator import run_agent_pipeline
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import collect
from .schema import COIN_POOL, Evidence, QuestionType, Report


def run(coin: str, query: str, qtype: QuestionType,
        offline: bool = False, data_dir=None,
        _log: ExecutionLog | None = None) -> tuple[Report, list[Evidence], ExecutionLog]:
    """跑完整管線：collect → 多步 agent 推理 → 報告。回傳 (report, evidence, log)。

    _log：可傳入外部 ExecutionLog（供 run_comparison 共用同一 log）；
          None 時自行建立新 log（原始行為）。
    """
    coin = coin.upper()
    log = _log if _log is not None else ExecutionLog()
    log.record("ingestion.collect", params={"coin": coin, "offline": offline})
    _failed: list[str] = []
    docs = collect(query, coin=coin, offline=offline, data_dir=data_dir, _failed=_failed)
    if not docs:
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    report, evidence = run_agent_pipeline(
        query, coin, qtype, docs,
        client=BedrockClient(offline=offline), log=log,
    )
    # 將 collect 階段失敗的來源名稱補入 report.limits，讓評審可見資料缺口
    for src_name in _failed:
        report.limits.append(f"{src_name} 來源無法取得，已跳過（逾時或連線失敗）。")
    return report, evidence, log


def run_comparison(
    coin_a: str,
    coin_b: str,
    query: str,
    offline: bool = False,
    data_dir=None,
) -> tuple[Report, list[Evidence], Report, list[Evidence], ExecutionLog]:
    """比較分析：各跑一次完整 pipeline，共用 ExecutionLog，回傳並列結果。

    Args:
        coin_a:   幣種 A（須在 COIN_POOL）
        coin_b:   幣種 B（須在 COIN_POOL，且不與 A 相同）
        query:    分析問題
        offline:  是否離線模式
        data_dir: OHLCV 資料目錄（可選）

    Returns:
        (report_a, evidence_a, report_b, evidence_b, log)

    Raises:
        ValueError: 幣種不合法或兩個幣種相同
    """
    coin_a, coin_b = coin_a.upper(), coin_b.upper()
    if coin_a not in COIN_POOL:
        raise ValueError(f"幣種 {coin_a} 須為 {COIN_POOL} 之一")
    if coin_b not in COIN_POOL:
        raise ValueError(f"幣種 {coin_b} 須為 {COIN_POOL} 之一")
    if coin_a == coin_b:
        raise ValueError("comparison 需兩個不同幣種，目前兩個幣種相同")

    log = ExecutionLog()
    log.record("comparison.start", params={"coin_a": coin_a, "coin_b": coin_b})

    report_a, evidence_a, _ = run(
        coin_a, query, QuestionType.COMPARISON, offline, data_dir, _log=log
    )
    report_b, evidence_b, _ = run(
        coin_b, query, QuestionType.COMPARISON, offline, data_dir, _log=log
    )

    log.record(
        "comparison.done",
        summary=f"{coin_a} vs {coin_b} 兩輪 pipeline 完成；"
                f"evidence A={len(evidence_a)} B={len(evidence_b)}",
    )
    return report_a, evidence_a, report_b, evidence_b, log
