"""Unit tests for issue #863 Bedrock verification scripts.

Tests the deterministic helper functions used by the verification scripts,
without requiring AWS credentials or Bedrock access.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "src"))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: claim_id regex tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaimIdRegex:
    """Test CLAIM_ID_RE from verify_traceability.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from verify_traceability import CLAIM_ID_RE
        self.re = CLAIM_ID_RE

    def test_basic_numeric_index(self):
        """Format: doc_id#0"""
        assert self.re.findall("price_btc_001#0") == ["price_btc_001#0"]

    def test_llm_prefix(self):
        """Format: doc_id#llm3"""
        assert self.re.findall("news_eth_002#llm3") == ["news_eth_002#llm3"]

    def test_doc_id_with_dash(self):
        """Format: doc-with-dash#llm12"""
        assert self.re.findall("doc-with-dash#llm12") == ["doc-with-dash#llm12"]

    def test_multi_digit_index(self):
        """Format: onchain_sol_005#llm15"""
        assert self.re.findall("onchain_sol_005#llm15") == ["onchain_sol_005#llm15"]

    def test_no_hash_no_match(self):
        """Strings without # should not match."""
        assert self.re.findall("no_hash_here") == []

    def test_hash_without_number_no_match(self):
        """Hash followed by non-digit should not match."""
        assert self.re.findall("doc#abc") == []

    def test_multiple_in_text(self):
        """Extract multiple claim_ids from narrative text."""
        text = (
            "根據 price_btc_001#llm0 顯示的價格走勢，"
            "以及 news_btc_002#llm1 報導的機構動態，"
            "結合 onchain_btc_003#0 的鏈上數據..."
        )
        found = self.re.findall(text)
        assert len(found) == 3
        assert "price_btc_001#llm0" in found
        assert "news_btc_002#llm1" in found
        assert "onchain_btc_003#0" in found

    def test_embedded_in_brackets(self):
        """claim_id inside brackets (common in reports)."""
        text = "[price_btc_001#0, news_btc_002#llm1]"
        found = self.re.findall(text)
        assert len(found) == 2

    def test_empty_string(self):
        assert self.re.findall("") == []


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: offline marker detection tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfflineMarkerDetection:
    """Test _check_offline_markers from verify_traceability.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from verify_traceability import _check_offline_markers, OFFLINE_MARKERS
        self.check = _check_offline_markers
        self.markers = OFFLINE_MARKERS

    def test_no_markers_in_normal_text(self):
        """Normal narrative should not trigger offline detection."""
        text = "BTC 近期走勢偏多，機構持續買入，鏈上活躍度上升。"
        assert self.check(text) == []

    def test_chinese_offline_marker(self):
        """Chinese offline marker should be detected."""
        text = "本次未執行線上模型生成；結論由結構化規則與可追溯證據產生。"
        found = self.check(text)
        assert len(found) >= 1

    def test_english_offline_marker(self):
        """English offline marker should be detected."""
        text = "No online model generation was performed for this run."
        found = self.check(text)
        assert len(found) >= 1

    def test_offline_placeholder(self):
        """[OFFLINE] placeholder should be detected."""
        text = "Result: [OFFLINE] — no model available"
        found = self.check(text)
        assert "[OFFLINE]" in found

    def test_degradation_text_detected(self):
        """Degradation marker should be detected."""
        text = "行文服務暫時無法使用,以下為結構化判斷"
        found = self.check(text)
        assert len(found) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3: fixture building tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureBuilding:
    """Test _build_fixture_docs from verify_traceability.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from verify_traceability import _build_fixture_docs
        self.build = _build_fixture_docs

    def test_produces_minimum_docs(self):
        """Should produce at least 5 documents."""
        docs = self.build("BTC")
        assert len(docs) >= 5

    def test_covers_three_kinds(self):
        """Should include price, news, and onchain kinds."""
        docs = self.build("BTC")
        kinds = {d.kind for d in docs}
        assert "price" in kinds
        assert "news" in kinds
        assert "onchain" in kinds

    def test_docs_have_valid_ids(self):
        """Each doc should have a non-empty id."""
        docs = self.build("ETH")
        for d in docs:
            assert d.id, f"Document has empty id: {d}"
            assert len(d.id) > 0

    def test_docs_have_text(self):
        """Each doc should have non-empty text."""
        docs = self.build("BTC")
        for d in docs:
            assert d.text, f"Document {d.id} has empty text"

    def test_docs_have_timestamps(self):
        """Each doc should have a positive timestamp."""
        docs = self.build("BTC")
        for d in docs:
            assert d.ts > 0, f"Document {d.id} has ts={d.ts}"

    def test_coin_in_meta(self):
        """Price docs should have coin in meta."""
        docs = self.build("SOL")
        price_docs = [d for d in docs if d.kind == "price"]
        for d in price_docs:
            assert d.meta.get("coin") == "SOL"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4: verify_bedrock env check tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyBedrockEnvChecks:
    """Test _check_env_vars from verify_bedrock.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from verify_bedrock import _check_env_vars
        self.check = _check_env_vars

    def test_all_set(self, monkeypatch):
        """When all required vars are set, _all_required_set should be True."""
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
        monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "test-haiku")
        result = self.check()
        assert result["_all_required_set"] is True
        assert result["AWS_REGION"]["status"] == "set"
        assert result["BEDROCK_MODEL_ID"]["status"] == "set"

    def test_missing_required(self, monkeypatch):
        """When a required var is missing, _all_required_set should be False."""
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        result = self.check()
        assert result["_all_required_set"] is False
        assert result["BEDROCK_MODEL_ID"]["status"] == "missing"

    def test_optional_not_required(self, monkeypatch):
        """Optional vars missing should not affect _all_required_set."""
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
        monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)
        result = self.check()
        assert result["_all_required_set"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5: error classification tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorClassification:
    """Test _classify_error_type from smoke_test_bedrock_extended.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from smoke_test_bedrock_extended import _classify_error_type
        self.classify = _classify_error_type

    def test_credential_error(self):
        exc = Exception("Unable to locate credential")
        assert self.classify(exc) == "credential"

    def test_permission_error(self):
        exc = Exception("Access denied: not authorized to perform bedrock:InvokeModel")
        assert self.classify(exc) == "permission"

    def test_model_not_found(self):
        exc = Exception("Model does not exist: bad-model-id")
        assert self.classify(exc) == "model-not-found"

    def test_timeout_error(self):
        exc = Exception("Read timed out after 30 seconds")
        assert self.classify(exc) == "timeout"

    def test_unknown_error(self):
        exc = ValueError("some random error")
        assert "unknown" in self.classify(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6: narrative layer verification (unit-level)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNarrativeLayerVerification:
    """Test verify_narrative_layers with mock Report objects."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from verify_traceability import verify_narrative_layers
        self.verify = verify_narrative_layers

    def test_complete_report(self):
        """A report with all layers should pass."""
        class MockReport:
            market_judgment = "BTC 方向偏多（校準信心 0.62，2 個獨立來源佐證）。"
            facts = ["BTC close=74449", "鏈上活躍地址上升 12%"]
            inferences = [
                "方向偏多，2 個獨立來源佐證。",
                "根據 price_btc_001#llm0 和 news_btc_002#llm1 的分析結果，機構買盤持續流入。"
            ]
            cross_source_signal = None
            key_basis = []
            limits = []

        result = self.verify(MockReport())
        assert result["has_facts"] is True
        assert result["has_inferences"] is True
        assert result["has_judgment"] is True
        assert result["narrative_has_layers"] is True
        assert result["offline_markers_absent"] is True

    def test_offline_report_detected(self):
        """A report with offline marker should be flagged."""
        class MockReport:
            market_judgment = "BTC 中性判斷"
            facts = ["price data"]
            inferences = ["本次未執行線上模型生成；結論由結構化規則與可追溯證據產生。"]
            cross_source_signal = None
            key_basis = []
            limits = []

        result = self.verify(MockReport())
        assert result["offline_markers_absent"] is False
        assert len(result["offline_markers_found"]) > 0

    def test_empty_report(self):
        """A report with no content should not pass."""
        class MockReport:
            market_judgment = ""
            facts = []
            inferences = []
            cross_source_signal = None
            key_basis = []
            limits = []

        result = self.verify(MockReport())
        assert result["narrative_has_layers"] is False
