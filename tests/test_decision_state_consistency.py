"""CEO Round 2 終批修復（PR #103，窮舉終審 4 條）驗收測試。

涵蓋範圍：
1. [HIGH] legacy 快照／未知 enum 值一律正規化為 normal（SSR 側：
   `web._normalize_decision_state` / `scripts/fetch_scheduler.py::_render_overview_html`）。
2. [MEDIUM] low_confidence 顏色語意統一（`web._decision_color`，
   `_conf_gauge`／`_cmp_conf`（比較頁）共用）。
3. [MEDIUM] 措辭清掃：repo 級斷言禁止使用者可見文案殘留「信心」。
4. [LOW] 跨面板 invariant：normal/low_confidence/abstain × 首頁卡（SSR）/
   內頁 gauge（SSR）/比較頁 的 hero 選擇 + 配色 parity；legacy/未知
   fallback；比較頁單份證據區、單幣頁保留證據區。

React 側對應測試見 `frontend/src/lib/decisionState.test.ts`。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from trustforge import web
from trustforge.schema import Report

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_report(
    coin: str = "BTC",
    decision_state: str = "normal",
    calibrated_confidence: float = 0.3,
    confidence: float = 0.8,
) -> Report:
    return Report(
        coin=coin,
        question_type="multi_source",
        question="test",
        market_judgment="偏多",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=confidence,
        limits=[],
        could_flip=[],
        contrarian=[],
        generated_at="2026-07-01T00:00:00Z",
        calibrated_confidence=calibrated_confidence,
        decision_state=decision_state,
    )


# ---------------------------------------------------------------------------
# #1 [HIGH] legacy 快照／未知 enum 值一律正規化為 normal
# ---------------------------------------------------------------------------

class TestNormalizeDecisionState:
    @pytest.mark.parametrize("known", ["abstain", "low_confidence", "normal"])
    def test_known_states_pass_through(self, known):
        assert web._normalize_decision_state(known) == known

    @pytest.mark.parametrize(
        "raw", [None, "", "hold", "high_confidence", "weird_future_state"]
    )
    def test_missing_or_unknown_normalizes_to_normal(self, raw):
        assert web._normalize_decision_state(raw) == "normal"


class TestConfGaugeLegacyFallback:
    def test_conf_gauge_two_arg_backward_compatible(self):
        """既有測試（test_web_dark_theme.py）用 2-positional-arg 呼叫，簽名
        不能破壞。"""
        out = web._conf_gauge(0.91, "高信心測試標籤")
        assert "tf-conf-wrap" in out

    @pytest.mark.parametrize("raw_state", [None, "", "hold", "unknown_future_state"])
    def test_legacy_or_unknown_decision_state_behaves_like_normal(self, raw_state):
        """缺失／未知 decision_state 傳入 `_conf_gauge`，hero 選擇與配色都要
        跟明確傳 `"normal"` 時完全一致（同一份輸出）。"""
        out_legacy = web._conf_gauge(0.40, "測試標籤", 0.40, raw_state)
        out_normal = web._conf_gauge(0.40, "測試標籤", 0.40, "normal")
        assert out_legacy == out_normal


class TestRenderOverviewHtmlLegacyFallback:
    """`scripts/fetch_scheduler.py::_render_overview_html`：legacy 快照缺
    `decision_state` key，或帶未知字面值，正規化為 normal 顯示——跟 SSR
    `_conf_gauge`／React `normalizeDecisionState` 同一套 fallback 規則。"""

    @staticmethod
    def _import_fetch_scheduler():
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import fetch_scheduler  # noqa: E402

        return fetch_scheduler

    def _snapshot(self, coin="BTC", extra=None):
        base = {
            "coin": coin,
            "trust_score": 0.62,
            "direction": "偏多",
            "calibrated_confidence": 0.55,
            "generated_at": "2026-07-01T00:00:00Z",
        }
        base.update(extra or {})
        return base

    def test_missing_decision_state_key_renders_as_normal(self):
        fs = self._import_fetch_scheduler()
        snap = self._snapshot()
        assert "decision_state" not in snap
        out = fs._render_overview_html([snap])
        assert "normal" in out
        assert "hold" not in out

    def test_unknown_decision_state_value_normalizes_to_normal(self):
        fs = self._import_fetch_scheduler()
        snap = self._snapshot(extra={"decision_state": "hold"})
        out = fs._render_overview_html([snap])
        # 顯示文字正規化——不把 legacy 舊值原樣印出
        assert "hold" not in out
        assert "normal" in out

    def test_missing_and_unknown_produce_identical_hero_output(self):
        """缺失 key 與明確帶 `"normal"` 值必須渲染出一致結果（hero 數字/
        配色皆相同），確認 fallback 完全等價，不只是「不炸」。"""
        fs = self._import_fetch_scheduler()
        snap_missing = self._snapshot()
        snap_explicit_normal = self._snapshot(extra={"decision_state": "normal"})
        out_missing = fs._render_overview_html([snap_missing])
        out_explicit = fs._render_overview_html([snap_explicit_normal])
        assert out_missing == out_explicit


# ---------------------------------------------------------------------------
# #2 [MEDIUM] low_confidence 顏色語意統一
# ---------------------------------------------------------------------------

class TestDecisionColorBoundaries:
    """`_decision_color(decision_state, hero)`：跟 React `bucketColor()` 同
    一套規則（見 `frontend/src/lib/decisionColor.ts`）。"""

    def test_abstain_always_red_regardless_of_value(self):
        assert web._decision_color("abstain", 0.0) == "#f85149"
        assert web._decision_color("abstain", 0.99) == "#f85149"

    def test_low_confidence_always_amber_regardless_of_value(self):
        assert web._decision_color("low_confidence", 0.0) == "#d9832a"
        assert web._decision_color("low_confidence", 0.40) == "#d9832a"
        assert web._decision_color("low_confidence", 0.99) == "#d9832a"

    def test_normal_buckets_by_value_with_exact_boundaries(self):
        assert web._decision_color("normal", 0.70) == "#3fb950"
        assert web._decision_color("normal", 0.69) == "#d9832a"
        assert web._decision_color("normal", 0.45) == "#d9832a"
        assert web._decision_color("normal", 0.44) == "#f85149"

    def test_040_boundary_differs_between_low_confidence_and_normal(self):
        """CEO 舉例的具體迴歸案例：同一份報告 0.40，low_confidence 態應為
        琥珀，normal 態應為紅——改前 SSR 兩處都只按數值分色，兩態同色。"""
        assert web._decision_color("low_confidence", 0.40) == "#d9832a"
        assert web._decision_color("normal", 0.40) == "#f85149"


class TestConfGaugeColorMatchesDecisionState:
    @pytest.mark.parametrize(
        "decision_state, calibrated, raw, expected_color",
        [
            ("abstain", 0.10, 0.30, "#f85149"),
            ("low_confidence", 0.40, 0.40, "#d9832a"),
            ("normal", 0.40, 0.40, "#f85149"),
            ("normal", 0.80, 0.80, "#3fb950"),
        ],
    )
    def test_conf_gauge_output_color(self, decision_state, calibrated, raw, expected_color):
        out = web._conf_gauge(calibrated, "標籤", raw, decision_state)
        assert expected_color in out


class TestCmpConfColorMatchesDecisionState:
    """比較頁 `_cmp_conf`（`_render_comparison` 內部）配色邊界值。"""

    def test_040_boundary_low_confidence_vs_normal_differ_in_comparison_page(self):
        report_a = _make_report(
            "BTC", decision_state="low_confidence", calibrated_confidence=0.40, confidence=0.40
        )
        report_b = _make_report(
            "ETH", decision_state="normal", calibrated_confidence=0.40, confidence=0.40
        )
        out = web._render_comparison(report_a, [], report_b, [], "BTC vs ETH")
        row_start = out.find("資訊完整度</td>")
        assert row_start != -1
        row_html = out[row_start : row_start + 900]
        assert 'color:#d9832a' in row_html  # BTC：low_confidence → 琥珀
        assert 'color:#f85149' in row_html  # ETH：normal 0.40 → 紅

    def test_abstain_always_red_in_comparison_page(self):
        report_a = _make_report(
            "BTC", decision_state="abstain", calibrated_confidence=0.90, confidence=0.90
        )
        report_b = _make_report("ETH", decision_state="normal", calibrated_confidence=0.90, confidence=0.90)
        out = web._render_comparison(report_a, [], report_b, [], "BTC vs ETH")
        row_start = out.find("資訊完整度</td>")
        row_html = out[row_start : row_start + 900]
        assert 'color:#f85149' in row_html  # BTC：abstain 高值仍紅
        assert 'color:#3fb950' in row_html  # ETH：normal 高值綠


# ---------------------------------------------------------------------------
# #3 [MEDIUM] 措辭清掃——repo 級斷言：使用者可見文案禁出現「信心」
# ---------------------------------------------------------------------------

class TestNoLegacyConfidenceWordingInUserFacingOutput:
    """白名單：內部術語（LLM prompt／instruction／review prompt／dev
    comment／docstring／frozen live-*.json fixture）刻意允許保留「信心」，
    不在使用者可見輸出範圍內，見 CLAUDE.md 交接摘要對這幾處的明確裁定。
    這裡只掃「真的會被渲染給使用者看」的輸出：
    - `Report` 的 `market_judgment`/`limits`/`inferences`/`confidence_label()`
    - `to_markdown()`/`comparison_to_markdown()`
    - `_render_report()`/`_render_comparison()`（SSR HTML，只排除
      `_render_cost_card` 等跟本次措辭無關但恰好含大量文字的區塊沒有必要，
      因為這兩個渲染函式輸出裡本來就不該出現「信心」二字）
    """

    def test_confidence_label_no_longer_uses_信心(self):
        for state in ("abstain", "low_confidence", "normal"):
            r = _make_report(decision_state=state, calibrated_confidence=0.3, confidence=0.3)
            assert "信心" not in r.confidence_label(), (state, r.confidence_label())

    def test_market_judgment_and_limits_no_信心_across_all_decision_states(self):
        # 直接驗證 schema 層 Report 手造 + to_markdown() 產出的字串（orchestrator
        # narrative builder 走完整 pipeline 太重，交由 tests/test_w4_calibration.py
        # 既有的 pipeline 級測試覆蓋 market_judgment/limits 實際生成內容）。
        for state in ("abstain", "low_confidence", "normal"):
            r = _make_report(
                decision_state=state,
                calibrated_confidence=0.3,
                confidence=0.3,
            )
            r.market_judgment = "測試市場判斷（不應含舊措辭）"
            r.limits = ["整體資訊完整度偏低，支撐證據不足以形成強判斷。"]
            md = r.to_markdown([])
            assert "信心" not in md, md

    def test_render_report_html_contains_no_legacy_信心_wording(self):
        r = _make_report(decision_state="low_confidence", calibrated_confidence=0.4, confidence=0.4)
        out = web._render_report(r, [])
        assert "信心" not in out, out

    def test_render_comparison_html_contains_no_legacy_信心_wording(self):
        report_a = _make_report("BTC", decision_state="abstain", calibrated_confidence=0.2, confidence=0.2)
        report_b = _make_report("ETH", decision_state="normal", calibrated_confidence=0.8, confidence=0.8)
        out = web._render_comparison(report_a, [], report_b, [], "BTC vs ETH")
        assert "信心" not in out, out


# ---------------------------------------------------------------------------
# #4 [LOW] 跨面板 invariant：table-driven，hero 選擇 + 配色 parity
# ---------------------------------------------------------------------------

DECISION_STATE_CASES = [
    # (decision_state, calibrated_confidence, confidence/trust_score, expected_hero, expected_color)
    ("normal", 0.30, 0.80, 0.80, "#3fb950"),
    ("low_confidence", 0.30, 0.80, 0.30, "#d9832a"),
    ("abstain", 0.30, 0.80, 0.30, "#f85149"),
]


class TestCrossPanelInvariant:
    """normal/low_confidence/abstain × {SSR _conf_gauge（內頁 gauge）,
    SSR _render_comparison（比較頁）, SSR fetch_scheduler 首頁卡} 的
    hero 選擇＋配色須一致；legacy/未知值 fallback 同 normal。"""

    @pytest.mark.parametrize(
        "decision_state, calibrated, raw, expected_hero, expected_color", DECISION_STATE_CASES
    )
    def test_conf_gauge_hero_and_color(
        self, decision_state, calibrated, raw, expected_hero, expected_color
    ):
        out = web._conf_gauge(calibrated, "標籤", raw, decision_state)
        assert f"{expected_hero:.2f}" in out
        assert expected_color in out

    @pytest.mark.parametrize(
        "decision_state, calibrated, raw, expected_hero, expected_color", DECISION_STATE_CASES
    )
    def test_comparison_page_hero_and_color(
        self, decision_state, calibrated, raw, expected_hero, expected_color
    ):
        report_a = _make_report(
            "BTC", decision_state=decision_state, calibrated_confidence=calibrated, confidence=raw
        )
        report_b = _make_report("ETH", decision_state="normal", calibrated_confidence=0.5, confidence=0.5)
        out = web._render_comparison(report_a, [], report_b, [], "BTC vs ETH")
        row_start = out.find("資訊完整度</td>")
        row_html = out[row_start : row_start + 900]
        assert f"（{expected_hero:.2f}）" in row_html
        assert f"color:{expected_color}" in row_html

    @pytest.mark.parametrize(
        "decision_state, calibrated, raw, expected_hero, expected_color", DECISION_STATE_CASES
    )
    def test_overview_card_hero_matches(self, decision_state, calibrated, raw, expected_hero, expected_color):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import fetch_scheduler

        snap = {
            "coin": "BTC",
            "trust_score": raw,
            "direction": "偏多",
            "calibrated_confidence": calibrated,
            "decision_state": decision_state,
            "generated_at": "2026-07-01T00:00:00Z",
        }
        out = fetch_scheduler._render_overview_html([snap])
        assert f"{expected_hero:.2f}" in out

    def test_legacy_and_unknown_state_behave_identically_to_explicit_normal_everywhere(self):
        """legacy（缺失）/未知值 在三個面板都要跟明確傳 `"normal"` 產生一致
        的 hero/配色結果——不是「不炸」而已，是「完全一致」。"""
        calibrated, raw = 0.30, 0.55

        # SSR gauge
        out_normal = web._conf_gauge(calibrated, "標籤", raw, "normal")
        out_unknown = web._conf_gauge(calibrated, "標籤", raw, "weird_future_state")
        out_missing = web._conf_gauge(calibrated, "標籤", raw, "")
        assert out_normal == out_unknown == out_missing

        # 比較頁
        report_normal = _make_report("BTC", decision_state="normal", calibrated_confidence=calibrated, confidence=raw)
        report_unknown = _make_report("BTC", decision_state="weird_future_state", calibrated_confidence=calibrated, confidence=raw)
        report_b = _make_report("ETH", decision_state="normal", calibrated_confidence=0.5, confidence=0.5)
        cmp_normal = web._render_comparison(report_normal, [], report_b, [], "q")
        cmp_unknown = web._render_comparison(report_unknown, [], report_b, [], "q")
        assert cmp_normal == cmp_unknown


# ---------------------------------------------------------------------------
# #4 [LOW] 比較頁單份證據區、單幣頁保留證據區
# ---------------------------------------------------------------------------

class TestEvidenceSectionCountInvariant:
    """#12 修復（雙層巢狀 details）延伸驗收：比較頁只有一份合併證據清單，
    內嵌的兩份單幣詳細分析不得各自重複渲染自己的證據清單；單幣 analyze
    頁（`_render_report` 預設行為）仍保留自己的證據清單，不受影響。"""

    SINGLE_REPORT_EVIDENCE_HEADING = "證據清單（信任橫條 · 點擊展開）"
    MERGED_EVIDENCE_HEADING = "2. 合併證據清單（標明幣種，點擊展開）"

    def test_single_coin_analyze_page_keeps_exactly_one_evidence_section(self):
        r = _make_report()
        out = web._render_report(r, [])
        assert out.count(self.SINGLE_REPORT_EVIDENCE_HEADING) == 1

    def test_comparison_page_has_exactly_one_merged_evidence_section_and_zero_embedded(self):
        report_a = _make_report("BTC")
        report_b = _make_report("ETH")
        out = web._render_comparison(report_a, [], report_b, [], "BTC vs ETH")
        assert out.count(self.SINGLE_REPORT_EVIDENCE_HEADING) == 0
        assert out.count(self.MERGED_EVIDENCE_HEADING) == 1


# ---------------------------------------------------------------------------
# #3 [MEDIUM] repo 級斷言：backend 原始碼「非 docstring」字串常數禁含「信心」
#
# 用 AST 而非文字 grep：Python `#` 註解本就不進 AST（天然排除，不需要另外
# 判斷），docstring（module/class/function body 第一句 Expr(Constant str)）
# 明確排除——只掃「真的會被組進 Report.market_judgment/limits/inferences/
# confidence_label()/to_markdown() 或 SSR HTML 的字串常數」，避免文字 grep
# 誤殺註解/docstring 裡合法保留的內部術語討論。
# ---------------------------------------------------------------------------

class TestRepoWideNoLegacyConfidenceWordingInSourceStrings:
    """白名單：僅放行明確核可、且確認「不會被使用者看到」的內部術語——
    `orchestrator.py` 的 LLM SYSTEM prompt（`SYSTEM` 常數）、abstain
    `_instruction` 字串、Step4 `_review_prompt` 片段，這三者只會送進
    `client.complete()` 當模型輸入，從未直接渲染給使用者（CEO 交接摘要
    明確裁定為 whitelist 範圍）。新增白名單項目前務必先確認該字串「真的
    不會被使用者看到」，否則就是真正的措辭殘留，應該改字而非加白名單。
    """

    _WHITELISTED_SUBSTRINGS = (
        "標註信心與限制",  # orchestrator.py SYSTEM prompt（LLM 系統指令）
        "校準信心過低",  # orchestrator.py abstain _instruction（LLM instruction）
        "信心：",  # orchestrator.py Step4 _review_prompt 片段（LLM review prompt）
    )

    _SCAN_TARGETS = (
        "src/trustforge/schema.py",
        "src/trustforge/web.py",
        "src/trustforge/agent/orchestrator.py",
        "src/trustforge/trust/scoring.py",
        "scripts/fetch_scheduler.py",
    )

    @staticmethod
    def _non_docstring_string_literals_containing(path: Path, needle: str):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstring_ids = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (
                isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and body
            ):
                first = body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstring_ids.add(id(first.value))
        hits = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
                and needle in node.value
            ):
                hits.append((node.lineno, node.value))
        return hits

    @pytest.mark.parametrize("relpath", _SCAN_TARGETS)
    def test_no_unwhitelisted_信心_string_literal_in_backend_source(self, relpath):
        path = REPO_ROOT / relpath
        hits = self._non_docstring_string_literals_containing(path, "信心")
        unwhitelisted = [
            (lineno, value)
            for lineno, value in hits
            if not any(w in value for w in self._WHITELISTED_SUBSTRINGS)
        ]
        assert unwhitelisted == [], (
            f"{relpath} 含未核可的「信心」措辭殘留（非 docstring/註解字串常數）——"
            f"若確認是使用者可見輸出請改字，若是新的內部術語請明確加入白名單並附理由：\n{unwhitelisted}"
        )
