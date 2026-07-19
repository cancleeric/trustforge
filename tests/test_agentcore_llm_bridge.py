"""AgentCore LLM bridge 的純本機回歸測試（不呼叫 AWS）。"""

import asyncio
import sys
from types import SimpleNamespace

from trustforge.agentcore_llm_bridge import AgentCoreLLMBridge, _BridgeConfig
from trustforge.bedrock import BedrockClient, BedrockConfig


class _FakeStreamingModel:
    config = {"model_id": "agentcore-test-model"}

    async def stream(self, **_kwargs):
        yield {"contentBlockDelta": {"delta": {"text": "hello "}}}
        yield {"contentBlockDelta": {"delta": {"text": "world"}}}
        yield {"metadata": {"usage": {"inputTokens": 7, "outputTokens": 2}}}


def _bridge() -> AgentCoreLLMBridge:
    return AgentCoreLLMBridge(
        _BridgeConfig(
            region="us-east-1",
            narrative_model_id="narrative-model",
            stance_model_id="stance-model",
            max_tokens=128,
        )
    )


def test_stream_text_collects_text_and_usage():
    text, usage = asyncio.run(_bridge()._stream_text(_FakeStreamingModel(), [], "system"))
    assert text == "hello world"
    assert usage == {"inputTokens": 7, "outputTokens": 2}


def test_extract_label_accepts_json_and_rejects_unrelated_text():
    assert _bridge()._extract_label('{"label":"contradiction"}') == "contradiction"
    assert _bridge()._extract_label("not a classification") is None


def test_complete_uses_stream_usage(monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "_narrative_model_instance", lambda: _FakeStreamingModel())
    result = bridge.complete("system", "prompt")
    assert result.text == "hello world"
    assert result.input_tokens == 7
    assert result.output_tokens == 2
    assert result.model_id == "agentcore-test-model"


def test_agentcore_stance_cost_uses_overridden_model(monkeypatch):
    class _FakeBridge:
        def classify_stance_raw(self, _system, _user_text):
            return "neutral", {"inputTokens": 11, "outputTokens": 3}

    fake_module = SimpleNamespace(build_bridge=lambda **_kwargs: _FakeBridge())
    monkeypatch.setitem(sys.modules, "trustforge.agentcore_llm_bridge", fake_module)
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE", "1")
    monkeypatch.setenv("AGENTCORE_MODEL_ID", "override-model")

    client = BedrockClient(
        config=BedrockConfig(model_id="narrative-model", stance_model_id="stance-model"),
        offline=False,
        stance_offline=False,
    )
    assert client.classify_stance_strict("A", "B") == "neutral"
    assert client.cost_events[-1]["model"] == "override-model"
