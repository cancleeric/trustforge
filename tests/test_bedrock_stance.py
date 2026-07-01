"""W1.5（#15）：`BedrockClient.classify_stance` 單元測試。

⛔ 本 PR 範圍限制：不真的呼叫 Bedrock。offline 路徑純本地驗證；線上路徑用
monkeypatch 換掉 `client._runtime()`，回傳假的 Converse API 回應（純 dict），
不打真 AWS，不花 credit。
"""
from __future__ import annotations

from trustforge.bedrock import BedrockClient, BedrockConfig


def test_classify_stance_offline_returns_neutral():
    client = BedrockClient(offline=True)
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_no_stance_model_id_returns_neutral():
    config = BedrockConfig(model_id="some-narrative-model", stance_model_id="")
    client = BedrockClient(config=config, offline=False)
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_runtime_exception_falls_back_to_neutral(monkeypatch):
    """呼叫失敗（逾時/憑證錯誤等）→ except 一律回 neutral，不 raise、不中斷管線。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _BoomRuntime:
        def converse(self, **kwargs):
            raise TimeoutError("simulated Bedrock timeout")

    monkeypatch.setattr(client, "_runtime", lambda: _BoomRuntime())
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_parses_tool_use_label(monkeypatch):
    """驗證強制 tool-use 結構化輸出的請求/解析路徑：
    temperature=0、toolChoice 強制指定 classify_stance 工具，並從
    toolUse.input.label 取出分類結果。
    """
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    captured: dict = {}

    class _FakeRuntime:
        def converse(self, **kwargs):
            captured.update(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "classify_stance", "input": {"label": "contradiction"}}}
                        ]
                    }
                }
            }

    monkeypatch.setattr(client, "_runtime", lambda: _FakeRuntime())
    result = client.classify_stance("A", "B")

    assert result == "contradiction"
    assert captured["modelId"] == "fake-stance-model"
    assert captured["inferenceConfig"]["temperature"] == 0
    assert captured["toolConfig"]["toolChoice"]["tool"]["name"] == "classify_stance"
    enum_values = captured["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"][
        "properties"
    ]["label"]["enum"]
    assert set(enum_values) == {"entailment", "contradiction", "neutral"}


def test_classify_stance_illegal_label_falls_back_to_neutral(monkeypatch):
    """防禦性驗證：若回應內容不含合法 label（理論上 toolConfig 已強制 enum，
    但仍防禦處理模型/回應異常），一律回 neutral，不 raise。
    """
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "classify_stance", "input": {"label": "bogus-label"}}}
                        ]
                    }
                }
            }

    monkeypatch.setattr(client, "_runtime", lambda: _FakeRuntime())
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_missing_tool_use_falls_back_to_neutral(monkeypatch):
    """回應內容裡完全沒有 toolUse block（異常回應格式）→ neutral，不 raise。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "unexpected plain text"}]}}}

    monkeypatch.setattr(client, "_runtime", lambda: _FakeRuntime())
    assert client.classify_stance("A", "B") == "neutral"
