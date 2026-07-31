"""新核心#2（gray docs/archive/plans/PLAN-multicore-worldfirst.md，task #25）：多維度信任拆解。

驗證重點：
1. `agent.orchestrator.aggregate_trust_by_kind` 按 source kind 分組正確聚合
   （純重新聚合既有 `Evidence.trust`，不重算信譽公式）。
2. 誠實標單源維度：`n_sources<=1` 的維度回傳 `single_source=True`。
3. 誠實顯示無資料：候選 kind 本輪完全沒有 evidence → `has_data=False`、
   `trust=None`，不補假分。
4. `web._render_trust_radar` 的 HTML 呈現忠實反映上述兩項誠實標記，且對
   使用者可控欄位（source/content_reference）做 html.escape。
5. $0：分維聚合／渲染不觸發任何額外 Bedrock 呼叫（跟關閉此功能時 bedrock.complete
   事件數一致，比照 `tests/test_w2_enable.py` 既有手法）。
6. 信任總分演算法不變：呼叫 `aggregate_trust_by_kind` 前後 `report.confidence`／
   `calibrated_confidence` 逐字相同（純額外呈現，不改總分）。
7. 固定軸契約（codex review）：回傳鍵集合嚴格等於 `KIND_REPUTATION` 全集，
   不在該表裡的 kind（空字串、schema drift、拼字錯誤）一律忽略、不動態
   加軸、不誤入其他既有維度——確保雷達軸跨報告可比較，不掩蓋分類錯誤。
"""
from __future__ import annotations

from trustforge import web
from trustforge.agent.orchestrator import aggregate_trust_by_kind, run_agent_pipeline
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import collect
from trustforge.schema import Evidence, QuestionType
from trustforge.trust.scoring import KIND_REPUTATION


def _ev(source: str, kind: str, trust: float, content_reference: str = "ref") -> Evidence:
    return Evidence(
        source=source,
        fetched_at="2026-01-01T00:00:00Z",
        content_reference=content_reference,
        related_claim="c1",
        kind=kind,
        trust=trust,
    )


# --- aggregate_trust_by_kind：分維聚合正確性 -------------------------------

def test_aggregate_by_kind_groups_and_averages_trust():
    """同 kind 的 evidence 取算術平均當該維度信任分，不同 kind 互不影響。"""
    evidence = [
        _ev("cryptopanic", "news", 0.8),
        _ev("coindesk", "news", 0.6),
        _ev("glassnode", "onchain", 0.9),
    ]
    dims = aggregate_trust_by_kind(evidence)
    assert dims["news"]["has_data"] is True
    assert dims["news"]["trust"] == round((0.8 + 0.6) / 2, 3)
    assert dims["news"]["n_evidence"] == 2
    assert dims["onchain"]["trust"] == 0.9


def test_aggregate_by_kind_counts_distinct_sources_not_evidence_count():
    """`n_sources` 是去重後的獨立來源數，跟 `n_evidence`（筆數）分開算。"""
    evidence = [
        _ev("cryptopanic", "news", 0.8),
        _ev("cryptopanic", "news", 0.7),  # 同來源第二筆
        _ev("coindesk", "news", 0.6),
    ]
    dims = aggregate_trust_by_kind(evidence)
    assert dims["news"]["n_evidence"] == 3
    assert dims["news"]["n_sources"] == 2
    assert dims["news"]["single_source"] is False


def test_aggregate_by_kind_single_source_honesty_flag():
    """單一來源維度（gray 抓出 regulatory 只有 SEC 1 源）必須誠實標 single_source。"""
    evidence = [
        _ev("sec-rss", "regulatory", 0.6),
        _ev("sec-rss", "regulatory", 0.5),
        _ev("reddit", "social", 0.35),
    ]
    dims = aggregate_trust_by_kind(evidence)
    assert dims["regulatory"]["n_sources"] == 1
    assert dims["regulatory"]["single_source"] is True
    assert dims["social"]["n_sources"] == 1
    assert dims["social"]["single_source"] is True


def test_aggregate_by_kind_multi_source_not_flagged_single():
    """3 個獨立來源的維度不能被誤標單一來源。"""
    evidence = [
        _ev("a", "onchain", 0.9),
        _ev("b", "onchain", 0.8),
        _ev("c", "onchain", 0.7),
    ]
    dims = aggregate_trust_by_kind(evidence)
    assert dims["onchain"]["n_sources"] == 3
    assert dims["onchain"]["single_source"] is False


def test_aggregate_by_kind_no_data_dimension_is_honest_not_faked():
    """候選 kind（KIND_REPUTATION 全集）本輪完全沒有 evidence
    → has_data=False、trust=None，不補 0 或其他假分（#24）。"""
    evidence = [_ev("cryptopanic", "news", 0.8)]
    dims = aggregate_trust_by_kind(evidence)
    for kind in KIND_REPUTATION:
        if kind == "news":
            continue
        assert dims[kind]["has_data"] is False, f"{kind} 本輪無資料，應誠實標 has_data=False"
        assert dims[kind]["trust"] is None, f"{kind} 無資料時 trust 不該補假分，收到 {dims[kind]['trust']!r}"
        assert dims[kind]["n_sources"] == 0
        assert dims[kind]["single_source"] is None


def test_aggregate_by_kind_candidate_set_covers_all_kind_reputation_keys():
    """候選維度＝KIND_REPUTATION 全集，就算完全沒有 evidence 也回傳全部候選鍵
    （雷達軸固定，不因某次分析剛好缺某類資料就整個消失）。"""
    dims = aggregate_trust_by_kind([])
    assert set(KIND_REPUTATION.keys()) <= set(dims.keys())
    assert all(d["has_data"] is False for d in dims.values())


def test_aggregate_by_kind_output_keys_strictly_equal_kind_reputation():
    """雷達軸嚴格等於 KIND_REPUTATION 全集——不多不少，跨報告可比較。
    （codex code review：舊版會把未知 kind 動態附加成額外軸，破壞可比性。）"""
    evidence = [
        _ev("cryptopanic", "news", 0.8),
        _ev("mystery-source", "mystery_kind", 0.5),
        _ev("typo-source", "newss", 0.9),  # 疑似把 "news" 拼錯
        _ev("nokind-source", "", 0.4),  # 空 kind
    ]
    dims = aggregate_trust_by_kind(evidence)
    assert set(dims.keys()) == set(KIND_REPUTATION.keys())


def test_aggregate_by_kind_unknown_kind_ignored_not_added_as_axis():
    """不在 KIND_REPUTATION 裡的未知 kind：忽略，不動態加軸、不誤入其他維度。"""
    evidence = [_ev("mystery-source", "mystery_kind", 0.5)]
    dims = aggregate_trust_by_kind(evidence)
    assert "mystery_kind" not in dims
    assert set(dims.keys()) == set(KIND_REPUTATION.keys())
    # 未知 kind 的 evidence 不會被塞進任何既有維度，也不影響它們的信任平均。
    for kind, d in dims.items():
        assert d["has_data"] is False, f"{kind} 不該因為未知 kind 的 evidence 誤標有資料"


def test_aggregate_by_kind_empty_kind_ignored_not_added_as_axis():
    """空字串 kind（schema drift / 忘記填）：忽略，不自成一軸。"""
    evidence = [_ev("nokind-source", "", 0.4), _ev("cryptopanic", "news", 0.8)]
    dims = aggregate_trust_by_kind(evidence)
    assert "" not in dims
    assert set(dims.keys()) == set(KIND_REPUTATION.keys())
    assert dims["news"]["n_evidence"] == 1
    assert dims["news"]["trust"] == 0.8


def test_aggregate_by_kind_misspelled_kind_ignored_not_folded_into_similar_axis():
    """拼錯的 kind（如 "newss"）不會被誤判、悄悄併入拼字相近的 "news" 維度，
    也不會自成一軸——避免掩蓋連接器分類錯誤。"""
    evidence = [_ev("typo-source", "newss", 0.9), _ev("cryptopanic", "news", 0.6)]
    dims = aggregate_trust_by_kind(evidence)
    assert "newss" not in dims
    assert set(dims.keys()) == set(KIND_REPUTATION.keys())
    assert dims["news"]["n_evidence"] == 1
    assert dims["news"]["trust"] == 0.6


def test_aggregate_by_kind_empty_evidence_all_no_data():
    dims = aggregate_trust_by_kind([])
    assert dims
    assert all(d["has_data"] is False and d["trust"] is None for d in dims.values())


# --- web._render_trust_radar：呈現層誠實度 ---------------------------------

def test_render_trust_radar_empty_dims_returns_empty_string():
    assert web._render_trust_radar({}, []) == ""


def test_render_trust_radar_shows_trust_score_and_source_count():
    evidence = [_ev("cryptopanic", "news", 0.8), _ev("coindesk", "news", 0.6)]
    dims = aggregate_trust_by_kind(evidence)
    out = web._render_trust_radar(dims, evidence)
    assert "新聞信任" in out
    assert "0.70" in out  # (0.8+0.6)/2
    assert "2 源" in out


def test_render_trust_radar_marks_single_source_dimension_honestly():
    evidence = [_ev("sec-rss", "regulatory", 0.6)]
    dims = aggregate_trust_by_kind(evidence)
    out = web._render_trust_radar(dims, evidence)
    assert "監管信任" in out
    assert "單一來源" in out


def test_render_trust_radar_does_not_mark_multi_source_dimension_as_single():
    evidence = [_ev("a", "onchain", 0.9), _ev("b", "onchain", 0.8), _ev("c", "onchain", 0.7)]
    dims = aggregate_trust_by_kind(evidence)
    out = web._render_trust_radar(dims, evidence)
    # 抓出鏈上信任那一列，確認沒有被貼上單一來源徽章
    idx = out.index("鏈上信任")
    row_end = out.index("</details>", idx)
    assert "單一來源" not in out[idx:row_end]


def test_render_trust_radar_shows_no_data_label_for_missing_dimension():
    dims = aggregate_trust_by_kind([_ev("cryptopanic", "news", 0.8)])
    out = web._render_trust_radar(dims, [_ev("cryptopanic", "news", 0.8)])
    assert "無資料" in out
    # dev_activity 本次沒有任何 evidence，必須落在無資料清單
    idx = out.index("開發活躍度信任")
    tail = out[idx: idx + 400]
    assert "無資料" in tail


def test_render_trust_radar_escapes_html_in_source_and_content():
    evidence = [_ev("<script>alert(1)</script>", "news", 0.5, content_reference="<b>x</b>")]
    dims = aggregate_trust_by_kind(evidence)
    out = web._render_trust_radar(dims, evidence)
    # 原始未跳脫的標籤字元不該出現在輸出裡（`source_display_name` 會先做
    # title-case 正規化，實際輸出大小寫不保證跟輸入一致，這裡只驗證跳脫
    # 這個安全性質，不綁死顯示名稱的大小寫轉換細節）。
    assert "<script>" not in out and "</script>" not in out
    assert "<b>x</b>" not in out
    assert "&lt;" in out and "&gt;" in out


# --- $0：不多打 Bedrock 呼叫 -------------------------------------------------

def _shared_docs():
    return collect("分析 BTC", coin="BTC", offline=True)


def test_aggregate_trust_by_kind_adds_zero_bedrock_calls():
    """分維聚合是純本地重新聚合既有 evidence，接不接線都不該多打 bedrock 呼叫。"""
    docs = _shared_docs()

    log_without = ExecutionLog(now_fn=lambda: 1.0)
    report, evidence = run_agent_pipeline(
        "分析 BTC", "BTC", QuestionType.MULTI_SOURCE, docs,
        client=BedrockClient(offline=True), log=log_without,
        run_scope_id="test-multidim-nocalls",
    )
    calls_without = len([e for e in log_without.events if e.get("tool") == "bedrock.complete"])

    # 呼叫分維聚合＋渲染（呈現層動作），確認不會回頭再打任何 bedrock/log 事件。
    dims = aggregate_trust_by_kind(evidence)
    web._render_trust_radar(dims, evidence)

    calls_after = len([e for e in log_without.events if e.get("tool") == "bedrock.complete"])
    assert calls_after == calls_without, "分維聚合／渲染不該產生任何新的 bedrock.complete 事件"
    assert calls_without > 0, "測試前提：pipeline 本身至少要有真實 bedrock.complete 事件才有驗證意義"


def test_aggregate_trust_by_kind_does_not_change_total_trust_score():
    """分維聚合是額外呈現，呼叫它前後 report 的信任總分完全不變。"""
    docs = _shared_docs()
    report, evidence = run_agent_pipeline(
        "分析 BTC", "BTC", QuestionType.MULTI_SOURCE, docs,
        client=BedrockClient(offline=True), log=ExecutionLog(now_fn=lambda: 1.0),
        run_scope_id="test-multidim-trust",
    )
    confidence_before = report.confidence
    calibrated_before = report.calibrated_confidence

    aggregate_trust_by_kind(evidence)  # 呼叫分維聚合

    assert report.confidence == confidence_before
    assert report.calibrated_confidence == calibrated_before
