"""最小 LLM 橋接器：讓 TrustForge 的 2 個 LLM 呼叫點可切換走 strands.BedrockModel。

設計要點（來自 CEO Eric 硬約束）：
- 這是純 code 改動，不改 TrustForge 本體架構，不動 deploy/、不動 src/trustforge/ 的
  資料管線/前端/CI。
- strands 的 `BedrockModel` 是 **async-only streaming** provider（見 strands 原始碼
  `strands/models/bedrock.py`：`stream()` 是非同步 generator，沒有同步的
  `invoke_model`/`converse` wrapper）。因此 bridge 內部用 `asyncio.run()` 包住
  `stream()` 呼叫，對外仍提供同步的 `complete()` / `classify_stance()`，與
  `BedrockClient` 現有用法對齊。
- **lazy import**：只在 `TRUSTFORGE_AGENTCORE=1` 時才 `import strands`，避免非
  AgentCore 環境（CI / 離線 / App Runner）因未安裝 strands 而炸。
- 依賴：不動 pyproject.toml 的 main dependencies。strands 只在 AgentCore runtime
  路徑被 import；若要走 AgentCore runtime 部署，需在 runtime 環境裝
  `strands-agents`（agentcore runtime 環境自帶或另裝），本機/CI 不強制。

⚠️ 真實 LLM 呼叫（workshop Bedrock model access）無法在本機驗證——本機無 AWS 憑證、
不在 workshop 環境，且本機未裝 strands。bridge 的「真實 LLM 回應」需由 CEO 在
workshop 環境親測。

strands BedrockModel 取回值結構（實測自原始碼）：
- text：消費 `stream()` 事件，過濾 `{"contentBlockDelta": {"delta": {"text": ...}}}`，
  把所有 text delta 串接。
- usage：在 `{"metadata": {"usage": {"inputTokens": N, "outputTokens": M}}}` 事件。
  （camelCase，與 boto3 converse API 一致，不同於 invoke_model 的 snake_case）
- tool_use（stance）：用 `stream(..., tool_choice={"tool": {"name": ...}})` 後解析
  `{"contentBlockStart" / delta}` 裡的 `toolUse.input`。此處直接用事件流解析，不依賴
  pydantic structured_output（避免引入額外 schema 依賴）。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from .bedrock import LLMResult

# AgentCore / strands 路徑用的預設 model id（與 CustomerSupport 同款）。
# 可被 env AGENTCORE_MODEL_ID 覆寫；未設時沿用 BedrockConfig 的 model_id 語意。
# AgentCore/strands 路徑預設 model id。
# workshop 帳號 (320566125702) 對 claude-sonnet-4-5-20250929 未開通 model access
# (AccessDenied / 需 Marketplace subscribe)，實測可用 global.anthropic.claude.sonnet-4-6。
# 比賽現場公告其他模型時用 env AGENTCORE_MODEL_ID 覆寫。
_DEFAULT_AGENTCORE_MODEL_ID = "global.anthropic.claude.sonnet-4-6"

# 關鍵錯誤：若 bridge 被啟用但環境沒 strands，要讓呼叫方快速失敗（對齊
# `_classify_stance_impl` 的 raise 哲學），不要靜默吞掉。
_STRANDS_IMPORT_ERROR = (
    "TRUSTFORGE_AGENTCORE=1 已啟用，但無法 import strands（未安裝 strands-agents）。"
    "請在 runtime 環境安裝 strands-agents 後再啟用此開關。"
)


def _import_strands():
    """lazy import strands，避免在 CI/離線/App Runner 環境因缺套件而炸。"""
    try:
        from strands.models.bedrock import BedrockModel  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取決於 runtime 是否裝 strands
        raise RuntimeError(_STRANDS_IMPORT_ERROR) from exc
    return BedrockModel


@dataclass
class _BridgeConfig:
    """bridge 用的輕量組態（只取 BedrockClient 需要的欄位）。

    不直接持有 BedrockClient，避免 import 時強綁定；呼叫方傳入必要值即可。
    """

    region: str
    narrative_model_id: str
    stance_model_id: str
    max_tokens: int
    agentcore_model_id: str | None = None


class AgentCoreLLMBridge:
    """用 strands.BedrockModel 實作 TrustForge 的 2 個 LLM 呼叫點。

    對外介面對齊 `BedrockClient` 現有用法：
    - `complete(system, prompt) -> LLMResult`
    - `classify_stance(a, b) -> str`

    內部用 `asyncio.run()` 包住 strands 的 async `stream()`（strands BedrockModel
    只有 async 實作）。
    """

    def __init__(self, cfg: _BridgeConfig):
        self._cfg = cfg
        self._narrative_model: object | None = None
        self._stance_model: object | None = None

    # -- model 惰性建立（strands BedrockModel 在建構時就會建 boto client）---------
    def _narrative_model_instance(self):
        if self._narrative_model is None:
            BedrockModel = _import_strands()
            model_id = self._cfg.agentcore_model_id or self._cfg.narrative_model_id or _DEFAULT_AGENTCORE_MODEL_ID
            self._narrative_model = BedrockModel(
                model_id=model_id,
                region_name=self._cfg.region,
                max_tokens=self._cfg.max_tokens,
            )
        return self._narrative_model

    def _stance_model_instance(self):
        if self._stance_model is None:
            BedrockModel = _import_strands()
            model_id = self._cfg.agentcore_model_id or self._cfg.stance_model_id
            # stance 用較小 max_tokens（與 bedrock.py 的 128 對齊）
            self._stance_model = BedrockModel(
                model_id=model_id,
                region_name=self._cfg.region,
                max_tokens=128,
                temperature=0,
            )
        return self._stance_model

    # -- 對齊 BedrockClient.complete -------------------------------------------
    def complete(self, system: str, prompt: str) -> LLMResult:
        model = self._narrative_model_instance()
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        text, usage = asyncio.run(self._stream_text(model, messages, system))
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("inputTokens", 0) or 0),
            output_tokens=int(usage.get("outputTokens", 0) or 0),
            model_id=model.config.get("model_id"),
        )

    # -- 對齊 BedrockClient.classify_stance ------------------------------------
    def classify_stance(self, a: str, b: str) -> str:
        model = self._stance_model_instance()
        # 複用 bedrock.py 的 few-shot 構造邏輯由呼叫方傳入 user_text / system，
        # 這裡直接接收已組好的 prompt（與 bedrock._classify_stance_impl 同構）。
        # 為避免重複 few-shot 字串，bridge 暴露 `classify_stance_raw(system, user_text)`，
        # classify_stance 由 bedrock.py 組好後呼叫 raw 版（見 bedrock.py 改動）。
        raise NotImplementedError(
            "classify_stance 請改呼叫 classify_stance_raw(system, user_text)；"
            "bridge 不重複持有 few-shot 字串。"
        )

    def classify_stance_raw(self, system: str, user_text: str) -> tuple[str, dict]:
        """取 stance label，回傳 (label, usage_dict)。

        label 為 entailment/contradiction/neutral，usage_dict 含 inputTokens/outputTokens。

        實作：優先用 **prompt 要求 JSON**（不強制 tool_use）。原因：workshop 帳號的
        Bedrock inference profile（`us.` / `global.` 前綴）多不支援 ConverseStream
        的 tool_use（報 ValidationException: model ... invalid），而純文字生成可用。
        故 bridge 改為「system 裡要求只回 JSON {\"label\": ...}」，從文字解析 label，
        對齊 bedrock._classify_stance_impl 的三類語意、但繞開 profile 的 tool 限制。
        若未來模型/帳號支援 tool_use，可在 build_bridge 開 tool 模式，此處保留
        _stream_tool_label 作備用。
        """
        from .bedrock import _STANCE_LABELS  # noqa: PLC0415

        model = self._stance_model_instance()
        # 在 user_text 後追加 JSON 約束（不破壞原 few-shot 語意）
        json_constraint = (
            "\n\n只回一行 JSON，不要解釋："
            '{"label": "entailment" | "contradiction" | "neutral"}'
        )
        messages = [{"role": "user", "content": [{"text": user_text + json_constraint}]}]
        label, usage = asyncio.run(self._stream_text(model, messages, system))
        # 從文字解析第一個合法 label
        parsed = self._extract_label(label or "")
        if parsed is not None:
            return parsed, usage
        raise ValueError("agentcore bridge: 回應內容缺少合法的 stance label")

    @staticmethod
    def _extract_label(text: str) -> str | None:
        """從模型回應文字取出合法 stance label。"""
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        from .bedrock import _STANCE_LABELS  # noqa: PLC0415

        # 嘗試直接 json.loads
        try:
            obj = json.loads(text.strip())
            if isinstance(obj, dict) and obj.get("label") in _STANCE_LABELS:
                return str(obj["label"])
        except (json.JSONDecodeError, ValueError):
            pass
        # 退路：regex 抓 "label": "xxx"
        m = re.search(r'"label"\s*:\s*"(entailment|contradiction|neutral)"', text)
        if m:
            return m.group(1)
        # 再退路：整段就是單一 label 字眼
        t = text.strip().lower()
        if t in _STANCE_LABELS:
            return t
        return None

    # -- strands async 事件流解析 ----------------------------------------------
    async def _stream_text(self, model, messages, system):
        """消費 stream() 事件，回傳 (text, usage_dict)。"""
        text_parts: list[str] = []
        usage: dict = {}
        async for event in model.stream(messages=messages, system_prompt=system):
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    text_parts.append(delta["text"])
            elif "metadata" in event and "usage" in event["metadata"]:
                usage = event["metadata"]["usage"] or {}
        return "".join(text_parts), usage

    async def _stream_tool_label(self, model, messages, system, tool_choice, tool_specs):
        """強制 tool_choice 後，從 stream() 事件取 toolUse.input['label']。

        回傳 (label, usage_dict)。
        """
        label: str | None = None
        usage: dict = {}
        async for event in model.stream(
            messages=messages,
            system_prompt=system,
            tool_choice=tool_choice,
            tool_specs=tool_specs,
        ):
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                tool_use = start.get("toolUse")
                if tool_use and tool_use.get("name") == tool_choice["tool"]["name"]:
                    label = str(tool_use.get("input", {}).get("label", "")).strip().lower()
            elif "contentBlockDelta" in event:
                # tool input 以 delta 形式分塊送來（見 convert_non_streaming_to_streaming）
                delta = event["contentBlockDelta"].get("delta", {})
                if "toolUse" in delta and "input" in delta["toolUse"]:
                    raw = delta["toolUse"]["input"]
                    # input 是 JSON 字串（見 bedrock.py convert_non_streaming_to_streaming）
                    if isinstance(raw, str):
                        import json  # noqa: PLC0415

                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict) and parsed.get("label"):
                                label = str(parsed["label"]).strip().lower()
                        except (json.JSONDecodeError, ValueError):
                            pass
                    elif isinstance(raw, dict) and raw.get("label"):
                        label = str(raw["label"]).strip().lower()
            elif "metadata" in event and "usage" in event["metadata"]:
                usage = event["metadata"]["usage"] or {}
        if label is None:
            raise ValueError("agentcore bridge: 未從 stream 解析到 toolUse.label")
        return label, usage


def build_bridge(region: str, narrative_model_id: str, stance_model_id: str, max_tokens: int,
                 agentcore_model_id: str | None = None) -> AgentCoreLLMBridge:
    """工廠函式：從 BedrockClient 的組態建立 bridge。"""
    return AgentCoreLLMBridge(
        _BridgeConfig(
            region=region,
            narrative_model_id=narrative_model_id,
            stance_model_id=stance_model_id,
            max_tokens=max_tokens,
            agentcore_model_id=agentcore_model_id,
        )
    )
