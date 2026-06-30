"""AWS Bedrock runtime — TrustForge 唯一的模型入口。

競賽硬約束：僅限使用 AWS 服務提供之基礎模型。所有 LLM 呼叫都必須經過這裡，
不得直接呼叫其他供應商或集團內部電話總機（anemone）。集中於此方便合規審查與換模型。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class BedrockConfig:
    region: str = os.getenv("AWS_REGION", "us-east-1")
    # 競賽現場（8/1）公告可用模型後填入；保持環境變數可配置，勿在程式碼寫死。
    model_id: str = os.getenv("BEDROCK_MODEL_ID", "")
    max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "1024"))


class BedrockClient:
    """薄封裝 boto3 bedrock-runtime 的 Messages API。

    離線模式（offline=True）不需 AWS 憑證，回傳佔位字串，方便在沒有 AWS 帳號時
    開發/測試信任層管線。
    """

    def __init__(self, config: BedrockConfig | None = None, offline: bool = False):
        self.config = config or BedrockConfig()
        self.offline = offline
        self._client = None

    def _runtime(self):
        if self._client is None:
            import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS

            self._client = boto3.client("bedrock-runtime", region_name=self.config.region)
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        """單輪文字生成。回傳模型文字輸出。"""
        if self.offline:
            return f"[OFFLINE] (model={self.config.model_id or 'unset'}) would answer:\n{prompt[:280]}"

        if not self.config.model_id:
            raise RuntimeError(
                "BEDROCK_MODEL_ID 未設定。競賽期間請設為 8/1 現場公告之 AWS Bedrock 模型 id。"
            )

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        resp = self._runtime().invoke_model(
            modelId=self.config.model_id,
            body=json.dumps(body),
        )
        payload = json.loads(resp["body"].read())
        # Bedrock messages 回應：content 為 block 陣列
        return "".join(b.get("text", "") for b in payload.get("content", []))
