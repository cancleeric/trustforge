"""共用分析管線 — CLI 與 web 服務都呼叫這裡，確保兩條路徑行為一致。

走顯式 3 步驟 agent 推理（run_agent_pipeline）：
  Step1 Claim 抽取(Bedrock/regex fallback) → Step2 信任評分聚合 → Step3 帶溯源行文 → Step4 限制複審。
"""
from __future__ import annotations

from typing import Any

from .agent.orchestrator import run_agent_pipeline
from .bedrock import BedrockClient, LLMResult
from .brand_logos import source_display_name
from .budget_guard import (
    daily_cap_exceeded,
    narrative_model_priced,
    online_stance_requested,
    release_request_budget,
    stance_model_priced,
    try_reserve_request_budget,
)
from .comparison_contract import ComparisonRunResult, build_comparison_report
from .execlog import ExecutionLog
from .ingestion.base import collect, execution_log_context
from .policy import PolicyExecutor
from .ports import BedrockLLMAdapter, LLMProvider, NullLLMAdapter, resolve_providers
from .schema import COIN_POOL, Evidence, QuestionType, Report
from .skills import run_skill_manifest


_DATA_MODES = ("live", "sample")
_LLM_MODES = ("off", "bedrock")


class _ProviderClient:
    """Adapt an LLMProvider to the BedrockClient duck type used by orchestrator."""

    def __init__(self, provider: LLMProvider, *, offline: bool, stance_offline: bool):
        self._provider = provider
        self.offline = offline
        self.stance_offline = stance_offline
        self.cost_events: list[dict] = []
        self.config = type("_ProviderClientConfig", (), {"model_id": None})()

    def complete(self, system: str, prompt: str) -> LLMResult:
        if self.offline:
            return LLMResult(text="[offline]", input_tokens=0, output_tokens=0, model_id=None)
        return LLMResult(
            text=self._provider.complete(system, prompt),
            input_tokens=0,
            output_tokens=0,
            model_id=None,
        )

    def extract_claims_with_llm(self, docs, log=None):
        from .trust.scoring import extract_claims

        return extract_claims(docs)

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        if self.stance_offline:
            return "neutral"
        return self._provider.classify_stance(claim_a, claim_b)


def _client_from_llm_provider(provider: LLMProvider | None, *, offline: bool, stance_offline: bool):
    if isinstance(provider, BedrockLLMAdapter):
        client = provider.client
        client.offline = offline
        client.stance_offline = stance_offline
        return client
    if provider is None:
        provider = NullLLMAdapter()
    return _ProviderClient(provider, offline=offline or isinstance(provider, NullLLMAdapter), stance_offline=stance_offline)


def _log_policy_consumers(
    log: ExecutionLog,
    policies: dict[str, Any],
) -> None:
    """Record the runtime consumers that received the frozen policy values."""
    consumer_by_family = {
        "source": "ingestion.collect",
        "analysis": "agent.orchestrator",
        "report": "report.postprocess",
        "evaluation": "quality.evaluation",
        "improvement": "improvement.diagnostics",
    }
    for family, consumer in sorted(consumer_by_family.items()):
        policy = policies.get(family)
        if policy is None:
            continue
        log.record(
            "policy.consumer",
            params={
                "family": family,
                "consumer": consumer,
                "policy": dict(getattr(policy, "__dict__", {})),
            },
            summary=f"{family} policy consumed by {consumer}",
        )


def _apply_report_policy(
    report: Report,
    policy: Any,
    log: ExecutionLog,
    *,
    origin: str | None = None,
) -> None:
    """Apply report-family policy to the presentation layer only."""
    max_sections = int(getattr(policy, "max_sections", 0) or 0)
    include_contrarian = bool(getattr(policy, "include_contrarian", True))
    effective_max_sections = 0 if origin == "baseline" else max_sections

    if effective_max_sections > 0:
        report.facts = report.facts[:effective_max_sections]
        report.inferences = report.inferences[:effective_max_sections]
        report.key_basis = report.key_basis[:effective_max_sections]
        report.limits = report.limits[:effective_max_sections]
        report.could_flip = report.could_flip[:effective_max_sections]
        report.contrarian = report.contrarian[:effective_max_sections]

    if not include_contrarian:
        report.contrarian = []

    log.record(
        "policy.consumer.report.applied",
        params={
            "max_sections": max_sections,
            "effective_max_sections": effective_max_sections,
            "include_contrarian": include_contrarian,
            "origin": origin,
        },
        summary="Report policy applied to presentation output",
    )


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
        llm_mode: str | None = None,
        force_stance_offline: bool = False) -> tuple[Report, list[Evidence], ExecutionLog]:
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

    force_stance_offline：#9 online-stance 預算配額硬化，供呼叫端（`web.py`
    的 per-IP online-stance 限流）在「單次請求」層級強制關閉 online-stance
    （不影響 narrative/`llm_mode`），用於「本 IP 線上分析請求過於頻繁」時
    誠實 degrade 到離線 stance，而非直接讓整個分析請求失敗。預設 `False`，
    對現有呼叫端行為無影響。

    #9 online-stance 預算配額硬化（每日全域成本上限 + online-stance 獨立
    開關）：見 `budget_guard` 模組 docstring。這兩項判斷優先權高於呼叫端
    傳入的 `data_mode`/`llm_mode`——即使呼叫端要求 `llm_mode="bedrock"`
    （`?live=1&token=`），只要今日累計成本已達 `TRUSTFORGE_BEDROCK_DAILY_
    USD_CAP` 上限，一律強制整輪 abstain（narrative + stance 都改離線），
    直到隔日 UTC 重置；`online_stance_requested()`（`TRUSTFORGE_ONLINE_
    STANCE` + 真 `BEDROCK_MODEL_ID`）則只在敘事本身已是離線的「真資料·$0」
    公開預設檔位（`data_mode="live", llm_mode="off"`）才會讓 stance 判斷
    改用真 Bedrock；兩者預設皆未設 → 行為與加入這兩項護欄前逐字相同。
    降級發生時，`report.limits` 會誠實補一句人類可讀說明（#24 不造假：
    照常回傳結果，只是清楚標明本次未用線上深度分析）。

    codex HIGH 追加（並行 TOCTOU）：`daily_cap_exceeded()` 讀的是「已完成
    run」的歷史帳本彙總，成本卻要等 pipeline 跑完才 append 回帳本——公開
    `/api/analyze` 並行請求全看到同一份「今日已花費」總額，會一起被放行
    直到全部完成才一起入帳，讓 $3/day 上限被並行撐爆。這裡在真的要放行
    Bedrock 前，額外用 `budget_guard.try_reserve_request_budget()` 做
    process-local 原子預留（見該函式 docstring），預留失敗（額度已被其他
    in-flight 請求佔滿，或帳本讀取失敗 fail-safe）視同「已達每日上限」，
    強制整輪離線；pipeline 結束（成功或失敗皆同）一律
    `release_request_budget()` 釋放預留。

    codex HIGH 追加（unpriced model 破壞 cap）：上述原子預留用的是固定
    保守估值（`request_max_cost_usd()`，預設 $0.05），估值本身只對
    `ledger.PRICING` 已登記的模型（sonnet/haiku）成立。narrative
    （`BEDROCK_MODEL_ID`）與 stance（`BEDROCK_HAIKU_MODEL_ID`）這次實際
    會用到的 model 若不在計價表登記，真實單價未知，固定估值可能嚴重
    低估、讓 cap 名存實亡——因此各自獨立 fail-closed：不放行、強制該段
    落離線 abstain，並歸還未實際用到的預留（見 `budget_guard.
    narrative_model_priced` / `stance_model_priced`）。
    """
    coin = coin.upper()
    data_mode, llm_mode = _resolve_modes(offline, data_mode, llm_mode)
    collect_offline = data_mode == "sample"
    llm_offline = llm_mode == "off"

    _cap_hit = daily_cap_exceeded()
    if _cap_hit:
        llm_offline = True

    _online_stance_wanted = (
        llm_mode == "off" and data_mode == "live" and online_stance_requested()
    )

    log = _log if _log is not None else ExecutionLog()
    _runtime_policies: dict[str, Any] = {}
    _runtime_policy_origins: dict[str, str | None] = {}
    # Freeze the selected outer-skill revisions at run start.  This is audit
    # data, not a runtime override of the deterministic Trust Layer.
    log.record("hermes.skills", params=run_skill_manifest(), summary="Hermes outer skill revisions frozen for this run")
    # Resolve typed outer-skill policies and log snapshot for auditability (R4/R5).
    try:
        _policy_executor = PolicyExecutor()
        _policy_executor.resolve_effective()
        _policy_snapshot = _policy_executor.snapshot_for_log()
        log.log_policy_snapshot(_policy_snapshot)
        for _family, _details in _policy_snapshot.get("policies", {}).items():
            if isinstance(_details, dict):
                _runtime_policy_origins[_family] = _details.get("origin")
        for _family in ("source", "analysis", "report", "evaluation", "improvement"):
            _runtime_policies[_family] = _policy_executor.get_policy(_family)
        _log_policy_consumers(log, _runtime_policies)
    except Exception:
        # Policy resolution is non-blocking for the pipeline — if skill artifacts
        # are missing or malformed, we log the failure but continue with defaults.
        log.record("policy.snapshot", params={"error": "policy_resolution_failed"}, summary="Policy resolution failed; continuing with defaults")
    log.record(
        "ingestion.collect",
        params={"coin": coin, "offline": collect_offline,
                "data_mode": data_mode, "llm_mode": llm_mode},
    )
    _failed: list[str] = []
    with execution_log_context(log):
        docs = collect(query, coin=coin, offline=collect_offline, data_dir=data_dir, _failed=_failed)
    if not docs:
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    # codex HIGH 追加（並行 TOCTOU）：只有「這次請求還沒被每日彙總 cap 擋下、
    # 且確實會嘗試真的打 Bedrock」（narrative 走 bedrock，或 online-stance
    # 想開且沒被 force_stance_offline 這個單請求層級開關否決）才需要原子
    # 預留這次的保守成本上界——force_stance_offline 已經否決的請求本來就
    # 不會打真 Bedrock，不佔用預留名額。必須緊貼在 collect 成功之後才預留
    # （不能在 collect 之前）：collect 失敗會提早 raise，永遠不會走到
    # `finally` 的 release，預留會永久洩漏，之後所有請求都會被誤判額度
    # 不足。
    _reservation: float | None = None
    _wants_bedrock = (not _cap_hit) and (
        llm_mode == "bedrock" or (_online_stance_wanted and not force_stance_offline)
    )
    if _wants_bedrock:
        _reservation = try_reserve_request_budget()
        if _reservation is None:
            # 額度不足（被其他 in-flight 並行請求先預留走），或帳本讀取
            # 失敗（fail-safe）：本次請求視同「已達每日上限」，走跟
            # `daily_cap_exceeded()` 為真時完全相同的 degrade 路徑與說明
            # ——對使用者而言都是「今日 Bedrock 預算已達上限」，差別只在於
            # 這次是被同一瞬間的並行請求搶走額度，而非帳本本身已達標。
            _cap_hit = True
            llm_offline = True

    # codex HIGH 追加（unpriced model 破壞 cap）：即使剛剛的原子預留放行，
    # 若 narrative 這次真的會用到的 model（`BEDROCK_MODEL_ID`）沒有在
    # `ledger.PRICING` 計價表登記，真實單價未知，固定 $0.05 保守估值可能
    # 嚴重低估真實花費，讓 cap 名存實亡——fail-closed：narrative 強制降級
    # 離線，比照 cap_hit 走同一套 degrade 路徑（誠實標記，見 #24）。這次
    # 請求根本沒真的被放行，剛拿到的預留立刻歸還，讓其他 in-flight 請求
    # 能用（不是「花掉」，是「從未真的打算花」）。
    _unpriced_hit = False
    if not llm_offline and llm_mode == "bedrock" and not narrative_model_priced():
        llm_offline = True
        _unpriced_hit = True
        if _reservation is not None:
            release_request_budget(_reservation)
            _reservation = None

    _online_stance_active = (
        llm_offline and _online_stance_wanted and not _cap_hit and not force_stance_offline
    )
    stance_offline = llm_offline and not _online_stance_active

    # 同上，stance 這次真的會用到的 model（`BEDROCK_HAIKU_MODEL_ID`）獨立
    # 檢查——narrative 與 stance 可能各自設定不同 model，各自 fail-closed，
    # 不能因為 narrative 過了就放行 stance（反之亦然）。stance 被這裡擋下
    # 時，若 narrative 這次也沒有真的打算打 Bedrock（`llm_offline` 已是
    # True），代表整次請求根本沒有真的花到預留額度，一併歸還。
    if not stance_offline and not stance_model_priced():
        stance_offline = True
        _unpriced_hit = True
        if _reservation is not None and llm_offline:
            release_request_budget(_reservation)
            _reservation = None

    _degrade_reason: str | None = None
    if _unpriced_hit:
        _degrade_reason = "unpriced_model"
    elif _cap_hit and (llm_mode == "bedrock" or _online_stance_wanted):
        _degrade_reason = "cap"
    elif _online_stance_wanted and force_stance_offline:
        _degrade_reason = "rate_limit"

    providers = resolve_providers(
        llm=BedrockLLMAdapter(
            client=BedrockClient(
                offline=llm_offline,
                stance_offline=stance_offline,
            )
        ),
        offline=llm_offline,
    )
    for resolution in providers.resolutions:
        if resolution.key == "llm":
            resolution.invoked = True
        log.record(
            "provider.resolve",
            params={
                "key": resolution.key,
                "configured": resolution.configured,
                "resolved": resolution.resolved,
                "invoked": resolution.invoked,
                "revision": resolution.revision,
                "fallback_reason": resolution.fallback_reason,
            },
            summary=f"provider {resolution.key}: {resolution.resolved}",
        )
    llm_client = _client_from_llm_provider(
        providers.llm,
        offline=llm_offline,
        stance_offline=stance_offline,
    )

    try:
        report, evidence = run_agent_pipeline(
            query, coin, qtype, docs,
            client=llm_client, log=log,
        )
        if "report" in _runtime_policies:
            _apply_report_policy(
                report,
                _runtime_policies["report"],
                log,
                origin=_runtime_policy_origins.get("report"),
            )
    finally:
        release_request_budget(_reservation)
    if _degrade_reason == "unpriced_model":
        report.limits.append(
            "本次未使用線上深度分析（所設定的 Bedrock 模型尚未登記計價，"
            "為避免預算失控已自動降級為離線分析）。"
        )
    elif _degrade_reason == "cap":
        report.limits.append(
            "本次未使用線上深度分析（今日 Bedrock 預算已達上限，已自動降級為"
            "離線分析，隔日重置）。"
        )
    elif _degrade_reason == "rate_limit":
        report.limits.append(
            "本次未使用線上深度分析（本 IP 線上分析請求過於頻繁，已自動降級"
            "為離線分析）。"
        )
    if data_mode == "sample":
        report.limits.append(
            "本次使用離線示範樣本資料，不代表即時市場資料。"
        )
    if llm_mode == "off":
        report.limits.append(
            "本次未使用線上模型生成；推論由結構化規則與可追溯證據產生。"
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
        # docs/archive/plans/PLAN-source-branding.md：這句「未取得資料」清單以前直接
        # join 原始 `Evidence.source` slug（如 `coingecko-sentiment`），
        # 跟 evidence pill 是同一個「印裸 slug 給使用者看」的問題，一併修。
        _display_failed = [source_display_name(s) for s in _seen_failed]
        report.limits.append(
            "以下來源本輪未取得資料，不納入計算：" + "、".join(_display_failed) + "。"
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
    force_stance_offline: bool = False,
) -> ComparisonRunResult:
    """比較分析：各跑一次完整 pipeline，共用 ExecutionLog，回傳 ComparisonRunResult。

    ComparisonRunResult 支援 unpack 為 5-tuple ``(report_a, ev_a, report_b, ev_b, log)``，
    確保現有呼叫端向後相容。

    Args:
        coin_a:    幣種 A（須在 COIN_POOL）
        coin_b:    幣種 B（須在 COIN_POOL，且不與 A 相同）
        query:     分析問題
        offline:   是否離線模式（`data_mode`/`llm_mode` 未顯式指定時的推導來源，向後相容）
        data_dir:  OHLCV 資料目錄（可選）
        data_mode: "live"|"sample"（見 `run()`；預設 None 由 offline 推導）
        llm_mode:  "off"|"bedrock"（見 `run()`；預設 None 由 offline 推導）
        force_stance_offline: 見 `run()` 同名參數（#9 online-stance 預算配額
            硬化，per-IP 限流誠實 degrade 用）；兩幣都套用同一個決定，因為
            per-IP 限流是「整次請求」層級，不是逐幣獨立判斷。

    Returns:
        ComparisonRunResult（支援 unpack 為 5-tuple）

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

    # Resolve llm_mode from None (same default-resolution as run())
    _, resolved_llm_mode = _resolve_modes(offline, data_mode=None, llm_mode=llm_mode)

    report_a, evidence_a, _ = run(
        coin_a, query, QuestionType.COMPARISON, offline, data_dir, _log=log,
        data_mode=data_mode, llm_mode=llm_mode,
        force_stance_offline=force_stance_offline,
    )
    report_b, evidence_b, _ = run(
        coin_b, query, QuestionType.COMPARISON, offline, data_dir, _log=log,
        data_mode=data_mode, llm_mode=llm_mode,
        force_stance_offline=force_stance_offline,
    )

    log.record(
        "comparison.done",
        summary=f"{coin_a} vs {coin_b} 兩輪 pipeline 完成；"
                f"evidence A={len(evidence_a)} B={len(evidence_b)}",
    )

    comparison = build_comparison_report(
        coin_a=coin_a,
        coin_b=coin_b,
        query=query,
        report_a=report_a,
        report_b=report_b,
        evidence_a=evidence_a,
        evidence_b=evidence_b,
    )

    # CA-05: If Bedrock is available, enhance with LLM synthesis
    if resolved_llm_mode == "bedrock" and comparison is not None:
        try:
            from .comparison_synthesis import synthesize_comparison_with_bedrock

            bedrock_client = BedrockClient(offline=offline)
            comparison = synthesize_comparison_with_bedrock(
                bedrock_client, comparison, evidence_a, evidence_b
            )
        except Exception:
            # Bedrock failure → keep deterministic fallback
            pass

    return ComparisonRunResult(
        report_a=report_a,
        report_b=report_b,
        evidence_a=evidence_a,
        evidence_b=evidence_b,
        comparison=comparison,
        log=log,
    )
