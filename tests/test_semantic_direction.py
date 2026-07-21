"""Tests for trustforge.semantic_direction (Phase 2, Issue #368).

測試語意方向分析模組：LLM 回應解析、投票聚合、離線 graceful degradation。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from trustforge.semantic_direction import (
    DirectionVote,
    _parse_llm_response,
    aggregate_votes,
    analyze_direction,
)


# --- Fixtures: Mock BedrockClient -------------------------------------------


@dataclass
class MockLLMResult:
    """模擬 BedrockClient.complete() 回傳的 LLMResult。"""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str | None = "mock-model"


class MockBedrockClient:
    """模擬 BedrockClient，可控制 offline 狀態與回應內容。"""

    def __init__(self, offline: bool = False, responses: dict[str, str] | None = None):
        self.offline = offline
        self.responses = responses or {}
        self.call_count = 0
        self.last_prompts: list[str] = []

    def complete(self, system: str, prompt: str) -> MockLLMResult:
        self.call_count += 1
        self.last_prompts.append(prompt)
        # 根據 prompt 中的關鍵字判斷來源類型，回傳對應 response
        for key, response_text in self.responses.items():
            if key in prompt:
                return MockLLMResult(text=response_text)
        return MockLLMResult(text='{"direction": "neutral", "confidence": 0.5, "reasoning": "default"}')


class FailingBedrockClient:
    """模擬 BedrockClient，所有呼叫都拋例外。"""

    offline = False

    def complete(self, system: str, prompt: str) -> MockLLMResult:
        raise RuntimeError("Bedrock unavailable")


# --- test_parse_llm_response_valid -------------------------------------------


class TestParseLLMResponseValid:
    """合法 JSON 回應能正確解析。"""

    def test_simple_json(self):
        text = '{"direction": "bullish", "confidence": 0.8, "reasoning": "price up"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["direction"] == "bullish"
        assert result["confidence"] == 0.8
        assert result["reasoning"] == "price up"

    def test_bearish(self):
        text = '{"direction": "bearish", "confidence": 0.6, "reasoning": "volume down"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["direction"] == "bearish"
        assert result["confidence"] == 0.6

    def test_neutral(self):
        text = '{"direction": "neutral", "confidence": 0.3, "reasoning": "sideways"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["direction"] == "neutral"
        assert result["confidence"] == 0.3

    def test_json_with_surrounding_text(self):
        """LLM 回應前後有多餘文字時仍能解析。"""
        text = 'Here is my analysis:\n{"direction": "bullish", "confidence": 0.9, "reasoning": "strong"}\nDone.'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["direction"] == "bullish"
        assert result["confidence"] == 0.9

    def test_missing_confidence_defaults_to_0_5(self):
        """缺少 confidence 欄位時預設 0.5。"""
        text = '{"direction": "bullish", "reasoning": "up"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["confidence"] == 0.5

    def test_missing_reasoning_defaults_to_empty(self):
        """缺少 reasoning 欄位時預設空字串。"""
        text = '{"direction": "bearish", "confidence": 0.7}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["reasoning"] == ""

    def test_confidence_clamped_to_1(self):
        """confidence > 1 時 clamp 到 1.0。"""
        text = '{"direction": "bullish", "confidence": 1.5, "reasoning": "x"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["confidence"] == 1.0

    def test_confidence_clamped_to_0(self):
        """confidence < 0 時 clamp 到 0.0。"""
        text = '{"direction": "bearish", "confidence": -0.3, "reasoning": "x"}'
        result = _parse_llm_response(text)
        assert result is not None
        assert result["confidence"] == 0.0


# --- test_parse_llm_response_invalid -----------------------------------------


class TestParseLLMResponseInvalid:
    """非法回應回傳 None。"""

    def test_empty_string(self):
        assert _parse_llm_response("") is None

    def test_plain_text(self):
        assert _parse_llm_response("I think it's bullish") is None

    def test_invalid_direction(self):
        text = '{"direction": "sideways", "confidence": 0.5, "reasoning": "x"}'
        assert _parse_llm_response(text) is None

    def test_malformed_json(self):
        text = '{"direction": "bullish", confidence: 0.5}'
        assert _parse_llm_response(text) is None

    def test_non_json_with_braces(self):
        text = 'The result is {unclear} at this point.'
        assert _parse_llm_response(text) is None

    def test_none_direction(self):
        text = '{"direction": null, "confidence": 0.5}'
        assert _parse_llm_response(text) is None


# --- test_aggregate_votes_bullish_wins ----------------------------------------


class TestAggregateVotesBullishWins:
    """bullish 加權顯著勝出時回傳 bullish。"""

    def test_single_strong_bullish(self):
        votes = [
            DirectionVote("price", "bullish", 0.9, "up"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "bullish"
        assert conf > 0.0

    def test_multiple_bullish_dominates(self):
        votes = [
            DirectionVote("price", "bullish", 0.8, "up"),
            DirectionVote("news", "bullish", 0.7, "positive"),
            DirectionVote("onchain", "bearish", 0.3, "outflow"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "bullish"
        # bullish_w=1.5, bearish_w=0.3; 1.5 > 0.3*1.3=0.39 ✓
        assert conf == pytest.approx(1.5 / 1.8, rel=1e-3)


# --- test_aggregate_votes_bearish_wins ----------------------------------------


class TestAggregateVotesBearishWins:
    """bearish 加權顯著勝出時回傳 bearish。"""

    def test_single_strong_bearish(self):
        votes = [
            DirectionVote("price", "bearish", 0.9, "down"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "bearish"

    def test_multiple_bearish_dominates(self):
        votes = [
            DirectionVote("price", "bearish", 0.8, "down"),
            DirectionVote("news", "bearish", 0.7, "negative"),
            DirectionVote("onchain", "bullish", 0.2, "inflow"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "bearish"


# --- test_aggregate_votes_neutral ---------------------------------------------


class TestAggregateVotesNeutral:
    """勢均力敵或無顯著勝出時回傳 neutral。"""

    def test_balanced_bullish_bearish(self):
        """bullish 和 bearish 旗鼓相當（差距 < 1.3 倍）→ neutral。"""
        votes = [
            DirectionVote("price", "bullish", 0.5, "up"),
            DirectionVote("news", "bearish", 0.5, "down"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "neutral"

    def test_all_neutral_votes(self):
        votes = [
            DirectionVote("price", "neutral", 0.5, "sideways"),
            DirectionVote("news", "neutral", 0.4, "mixed"),
        ]
        direction, conf = aggregate_votes(votes)
        assert direction == "neutral"

    def test_slight_bullish_not_enough(self):
        """bullish 只比 bearish 高一點（不到 1.3 倍）→ neutral。"""
        votes = [
            DirectionVote("price", "bullish", 0.6, "up"),
            DirectionVote("news", "bearish", 0.5, "down"),
        ]
        direction, conf = aggregate_votes(votes)
        # 0.6 > 0.5*1.3=0.65? No → neutral
        assert direction == "neutral"


# --- test_aggregate_votes_empty -----------------------------------------------


class TestAggregateVotesEmpty:
    """空投票列表回傳 ("neutral", 0.0)。"""

    def test_empty_list(self):
        direction, conf = aggregate_votes([])
        assert direction == "neutral"
        assert conf == 0.0


# --- test_analyze_direction_offline_returns_empty -----------------------------


class TestAnalyzeDirectionOffline:
    """離線模式（client.offline=True）不呼叫 LLM，回傳空列表。"""

    def test_offline_client_returns_empty(self):
        client = MockBedrockClient(offline=True)
        evidence = {"price": ["BTC close=50000"], "news": ["Bitcoin surges"]}
        votes = analyze_direction(evidence, client)
        assert votes == []
        assert client.call_count == 0

    def test_online_client_calls_llm(self):
        """線上模式會呼叫 LLM。"""
        client = MockBedrockClient(
            offline=False,
            responses={
                "OHLCV": '{"direction": "bullish", "confidence": 0.8, "reasoning": "trend up"}',
                "新聞": '{"direction": "bullish", "confidence": 0.7, "reasoning": "positive"}',
            },
        )
        evidence = {"price": ["BTC OHLCV data"], "news": ["BTC 新聞 positive"]}
        votes = analyze_direction(evidence, client)
        assert len(votes) == 2
        assert client.call_count == 2

    def test_max_3_calls(self):
        """最多呼叫 3 次 LLM（即使有 4 種來源類型）。"""
        client = MockBedrockClient(offline=False)
        evidence = {
            "price": ["data1"],
            "news": ["data2"],
            "onchain": ["data3"],
            "sentiment": ["data4"],
        }
        votes = analyze_direction(evidence, client)
        assert client.call_count == 3  # price + news + onchain; sentiment skipped

    def test_llm_failure_graceful(self):
        """LLM 呼叫失敗時不崩，只是沒有投票。"""
        client = FailingBedrockClient()
        evidence = {"price": ["data"], "news": ["data"]}
        votes = analyze_direction(evidence, client)
        assert votes == []

    def test_empty_evidence_skipped(self):
        """空的來源類型不觸發 LLM 呼叫。"""
        client = MockBedrockClient(offline=False)
        evidence = {"price": [], "news": ["something"]}
        votes = analyze_direction(evidence, client)
        # 只有 news 有內容，price 被跳過
        assert client.call_count == 1

    def test_unknown_source_type_skipped(self):
        """不在 SOURCE_TYPE_PROMPTS 中的類型不觸發呼叫。"""
        client = MockBedrockClient(offline=False)
        evidence = {"unknown_type": ["data"], "price": ["data"]}
        votes = analyze_direction(evidence, client)
        assert client.call_count == 1  # 只有 price
