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


_DATA_MODES = ("live", "sample")
_LLM_MODES = ("off", "bedrock")


def _resolve_modes(
    offline: bool, data_mode: str | None, llm_mode: str | None
) -> tuple[str, str]:
    """把舊 `offline` bool 與新 `data_mode`/`llm_mode` 統一成 (data_mode, llm_mode)。

    解耦重點：`offline` 只在對應參數未顯式指定時才用來推導預設值，
    讓呼叫端可以只調其中一項（例如 data_mode="live" + llm_mode="off"
    ＝真連接器＋免 Bedrock，credit-safe 的「真資料」檔），
    不再被單一 bool 綁死「樣本資料」與「Bedrock 開關」必須同進退。
    """
    if data_mode is None:
        data_mode = "sample" if offline else "live"
    if data_mode not in _DATA_MODES:
        raise ValueError(f"data_mode 須為 {_DATA_MODES} 之一，收到 {data_mode!r}")

    if llm_mode is None:
        llm_mode = "off" if offline else "bedrock"
    if llm_mode not in _LLM_MODES:
        raise ValueError(f"llm_mode 須為 {_LLM_MODES} 之一，收到 {llm_mode!r}")

    return data_mode, llm_mode


def run(coin: str, query: str, qtype: QuestionType,
        offline: bool = False, data_dir=None,
        _log: ExecutionLog | None = None,
        data_mode: str | None = None,
        llm_mode: str | None = None) -> tuple[Report, list[Evidence], ExecutionLog]:
    """跑完整管線：collect → 多步 agent 推理 → 報告。回傳 (report, evidence, log)。

    _log：可傳入外部 ExecutionLog（供 run_comparison 共用同一 log）；
          None 時自行建立新 log（原始行為）。

    data_mode / llm_mode：解耦「資料來源」與「Bedrock 開關」的獨立旗標。
        data_mode="live"|"sample"（預設 None，由 `offline` 推導：offline→sample）
        llm_mode ="off" |"bedrock"（預設 None，由 `offline` 推導：offline→off）
    未顯式傳入時完全等同舊行為（僅靠 `offline`）；對外簽名與預設值向後相容。
    三檔組合：
        offline=True（或 data_mode=sample, llm_mode=off）  → 離線示範，$0
        data_mode="live", llm_mode="off"                    → 真資料·$0（本次新增）
        offline=False（或 data_mode=live, llm_mode=bedrock）→ 真連接器 + 真 Bedrock
    """
    coin = coin.upper()
    data_mode, llm_mode = _resolve_modes(offline, data_mode, llm_mode)
    collect_offline = data_mode == "sample"
    llm_offline = llm_mode == "off"

    log = _log if _log is not None else ExecutionLog()
    log.record(
        "ingestion.collect",
        params={"coin": coin, "offline": collect_offline,
                "data_mode": data_mode, "llm_mode": llm_mode},
    )
    _failed: list[str] = []
    docs = collect(query, coin=coin, offline=collect_offline, data_dir=data_dir, _failed=_failed)
    if not docs:
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    report, evidence = run_agent_pipeline(
        query, coin, qtype, docs,
        client=BedrockClient(offline=llm_offline), log=log,
    )
    # 世界第一重寫 Phase 2「缺源優雅處理」：collect 階段失敗的來源（cache
    # miss / 429 / 逾時等）合併成**單一**中性句子補入 report.limits，讓評審
    # 看得到資料缺口、但不是一排逐來源的刺眼「無法取得，已跳過」——單一
    # 來源失敗（如 reddit 429）在多源分析裡是常態，不該讀起來像整頁出錯。
    # 去重（order-preserving），避免同來源多次失敗造成重複條目。
    _seen_failed: list[str] = []
    for src_name in _failed:
        if src_name not in _seen_failed:
            _seen_failed.append(src_name)
    if _seen_failed:
        report.limits.append(
            "以下來源本輪未取得資料，不納入計算：" + "、".join(_seen_failed) + "。"
        )
    return report, evidence, log


def run_comparison(
    coin_a: str,
    coin_b: str,
    query: str,
    offline: bool = False,
    data_dir=None,
    data_mode: str | None = None,
    llm_mode: str | None = None,
) -> tuple[Report, list[Evidence], Report, list[Evidence], ExecutionLog]:
    """比較分析：各跑一次完整 pipeline，共用 ExecutionLog，回傳並列結果。

    Args:
        coin_a:    幣種 A（須在 COIN_POOL）
        coin_b:    幣種 B（須在 COIN_POOL，且不與 A 相同）
        query:     分析問題
        offline:   是否離線模式（`data_mode`/`llm_mode` 未顯式指定時的推導來源，向後相容）
        data_dir:  OHLCV 資料目錄（可選）
        data_mode: "live"|"sample"（見 `run()`；預設 None 由 offline 推導）
        llm_mode:  "off"|"bedrock"（見 `run()`；預設 None 由 offline 推導）

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
        coin_a, query, QuestionType.COMPARISON, offline, data_dir, _log=log,
        data_mode=data_mode, llm_mode=llm_mode,
    )
    report_b, evidence_b, _ = run(
        coin_b, query, QuestionType.COMPARISON, offline, data_dir, _log=log,
        data_mode=data_mode, llm_mode=llm_mode,
    )

    log.record(
        "comparison.done",
        summary=f"{coin_a} vs {coin_b} 兩輪 pipeline 完成；"
                f"evidence A={len(evidence_a)} B={len(evidence_b)}",
    )
    return report_a, evidence_a, report_b, evidence_b, log
