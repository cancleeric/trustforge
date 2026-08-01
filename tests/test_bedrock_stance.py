"""W1.5（#15）：`BedrockClient.classify_stance` 單元測試。

⛔ 本 PR 範圍限制：不真的呼叫 Bedrock。offline 路徑純本地驗證；線上路徑用
monkeypatch 換掉 `client._stance_runtime()`（CEO/codex 對抗審修正後 stance 專用、
獨立短 timeout 的 client，見 bedrock.py 的 `_STANCE_READ_TIMEOUT_SEC`），
回傳假的 Converse API 回應（純 dict），不打真 AWS，不花 credit。
"""
from __future__ import annotations

import pytest

from trustforge.bedrock import BedrockClient, BedrockConfig


def test_classify_stance_offline_returns_neutral():
    client = BedrockClient(offline=True)
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_no_stance_model_id_returns_neutral():
    config = BedrockConfig(model_id="some-narrative-model", stance_model_id="")
    client = BedrockClient(config=config, offline=False)
    assert client.classify_stance("A", "B") == "neutral"


# ---------------------------------------------------------------------------
# #9 online-stance 預算配額硬化：`stance_offline` 與敘事 `offline` 解耦
# ---------------------------------------------------------------------------

def test_stance_offline_defaults_to_narrative_offline_when_not_specified():
    """未顯式傳入 `stance_offline` 時，向後相容：等同 `offline`（加入這個
    參數前的行為逐字不變）。"""
    assert BedrockClient(offline=True).stance_offline is True
    assert BedrockClient(offline=False).stance_offline is False


def test_classify_stance_respects_stance_offline_even_when_narrative_online(monkeypatch):
    """`offline=False`（敘事線上）但 `stance_offline=True` → stance 判斷仍必須
    fail-safe 回 neutral，且完全不呼叫 `_stance_runtime()`（不打真 AWS）。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False, stance_offline=True)

    def _boom_runtime():
        raise AssertionError("stance_offline=True 時不該建立/呼叫 _stance_runtime()")

    monkeypatch.setattr(client, "_stance_runtime", _boom_runtime)
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_goes_online_when_narrative_offline_but_stance_not(monkeypatch):
    """`offline=True`（敘事離線，$0）但 `stance_offline=False` → stance 判斷
    改走真呼叫路徑（用 monkeypatch 換掉 `_stance_runtime()`，不打真 AWS），
    這是 #9 online-stance 開關生效時的核心解耦行為。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=True, stance_offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "classify_stance", "input": {"label": "entailment"}}}
                        ]
                    }
                }
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    assert client.classify_stance("A", "B") == "entailment"
    # 敘事本身仍離線，不受影響
    assert client.offline is True


def test_classify_stance_runtime_exception_falls_back_to_neutral(monkeypatch):
    """呼叫失敗（逾時/憑證錯誤等）→ except 一律回 neutral，不 raise、不中斷管線。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _BoomRuntime:
        def converse(self, **kwargs):
            raise TimeoutError("simulated Bedrock timeout")

    monkeypatch.setattr(client, "_stance_runtime", lambda: _BoomRuntime())
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

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
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

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_missing_tool_use_falls_back_to_neutral(monkeypatch):
    """回應內容裡完全沒有 toolUse block（異常回應格式）→ neutral，不 raise。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "unexpected plain text"}]}}}

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    assert client.classify_stance("A", "B") == "neutral"


def test_classify_stance_request_uses_correct_model_and_min_max_tokens(monkeypatch):
    """守門測試（防 regression）：`classify_stance` 送出的請求必須用正確的
    stance 模型 id，且 `inferenceConfig["maxTokens"]` 不可低於 64——見
    bedrock.py 頂部註解：實測 maxTokens=32 會把 tool_use JSON 輸出截斷成空回應，
    導致每次都誤降級為 neutral（漏抓真矛盾）。本測試鎖住這個下限與模型 id，
    避免未來改動不小心把 maxTokens 調回過小的值。
    """
    client = BedrockClient(offline=False)  # 用預設 BedrockConfig，鎖住預設 stance_model_id

    captured: dict = {}

    class _FakeRuntime:
        def converse(self, **kwargs):
            captured.update(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "classify_stance", "input": {"label": "neutral"}}}
                        ]
                    }
                }
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    client.classify_stance("A", "B")

    assert captured["modelId"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert captured["inferenceConfig"]["maxTokens"] >= 64


# ── classify_stance_strict（HIGH 修正：gen 腳本專用，失敗一律 raise，不吞 neutral）


def test_classify_stance_strict_offline_raises():
    """offline client：`classify_stance` 回 neutral，但 strict 版必須 raise，
    不可讓呼叫端把「離線佔位」誤當成真實分類寫進持久化快取。
    """
    client = BedrockClient(offline=True)
    with pytest.raises(Exception):
        client.classify_stance_strict("A", "B")


def test_classify_stance_strict_no_stance_model_id_raises():
    config = BedrockConfig(model_id="some-narrative-model", stance_model_id="")
    client = BedrockClient(config=config, offline=False)
    with pytest.raises(Exception):
        client.classify_stance_strict("A", "B")


def test_classify_stance_strict_runtime_exception_raises_not_neutral(monkeypatch):
    """核心 HIGH 修正：runtime 逾時/異常時，strict 版必須把例外往上丟，
    不可吞成 neutral（否則 gen 腳本會把假 neutral 寫進 stance_cache.json）。
    """
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _BoomRuntime:
        def converse(self, **kwargs):
            raise TimeoutError("simulated Bedrock timeout")

    monkeypatch.setattr(client, "_stance_runtime", lambda: _BoomRuntime())
    with pytest.raises(TimeoutError):
        client.classify_stance_strict("A", "B")


def test_classify_stance_strict_illegal_label_raises(monkeypatch):
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

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    with pytest.raises(ValueError):
        client.classify_stance_strict("A", "B")


def test_classify_stance_strict_missing_tool_use_raises(monkeypatch):
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "unexpected plain text"}]}}}

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    with pytest.raises(ValueError):
        client.classify_stance_strict("A", "B")


def test_classify_stance_strict_success_returns_label(monkeypatch):
    """成功路徑：strict 版與非 strict 版解析行為一致，正常回傳合法 label。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "classify_stance", "input": {"label": "contradiction"}}}
                        ]
                    }
                }
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    assert client.classify_stance_strict("A", "B") == "contradiction"


# ── region/profile 相容性守門：競賽預設 region 為 us-east-1/us-west-2，
# 預設 stance profile 必須同步使用 us. profile，避免 zero-env production 失敗。


def test_default_region_compatible_with_default_stance_model_profile():
    """`BedrockConfig()` 的預設值必須自成一組可用組合。

    競賽環境指定主要部署 region 為 us-east-1/us-west-2，因此預設 stance model
    必須使用 `us.` cross-region inference profile；如果未來 region/profile 前綴
    再度不相容，這條測試要直接變紅。
    """
    config = BedrockConfig()

    assert config.stance_model_id.startswith("us.")
    assert config.region in {"us-east-1", "us-west-2"}


def test_default_narrative_model_id_uses_competition_us_profile():
    """敘事模型預設也必須維持競賽 us.* profile；仍可由 BEDROCK_MODEL_ID 覆寫。"""
    config = BedrockConfig()
    assert config.model_id == "us.anthropic.claude-sonnet-4-6"
