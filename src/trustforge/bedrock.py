"""AWS Bedrock runtime — TrustForge 唯一的模型入口。

競賽硬約束：僅限使用 AWS 服務提供之基礎模型。所有 LLM 呼叫都必須經過這裡，
不得直接呼叫其他供應商或集團內部電話總機（anemone）。集中於此方便合規審查與換模型。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ingestion.base import Document
    from .trust.scoring import Claim

# 僅客觀來源才能標記 claim_type=fact（反作弊：主觀社群/新聞不得宣稱是事實）
_OBJECTIVE_KINDS = frozenset({"price", "onchain", "regulatory"})


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

    # ------------------------------------------------------------------
    # Step 1: LLM-based Claim 抽取（Bedrock 呼叫 #1）
    # ------------------------------------------------------------------
    def extract_claims_with_llm(self, docs: list[Document]) -> list[Claim]:
        """從多源 Document 用 LLM 抽出結構化主張。

        離線模式或未設模型時 fall back 回 regex extract_claims，確保測試/離線 demo 不變。
        線上模式：Bedrock 呼叫 #1，輸出 claim_type + direction；
                  fact 類只來自客觀來源（price/onchain/regulatory），事後過濾。
        """
        # 延遲匯入：避免模組頂層循環依賴
        from .trust.scoring import Claim, extract_claims  # noqa: PLC0415

        if self.offline or not self.config.model_id:
            # 離線降級：使用 regex 切句；claim_type/direction 保持預設值
            return extract_claims(docs)

        # 建立文件列表（帶 id 方便 LLM 標注溯源）
        doc_map: dict[str, Document] = {}
        lines: list[str] = []
        for d in docs:
            doc_map[d.id] = d
            # 截斷過長文本以控制 token 用量
            snippet = d.text[:300].replace("\n", " ")
            lines.append(f"[{d.id}] kind={d.kind} source={d.source}: {snippet}")

        doc_block = "\n".join(lines)
        system_prompt = (
            "你是金融資訊分析師。請嚴格依據提供的文件，不引入外部知識。"
        )
        user_prompt = (
            "從以下加密市場文件中，抽出每個獨立的「市場主張」。\n\n"
            f"文件列表：\n{doc_block}\n\n"
            "輸出 JSON array，每條格式：\n"
            '{"claim": "主張原文", "claim_type": "fact|inference|opinion", '
            '"direction": "bullish|bearish|neutral", "source_doc_id": "文件id"}\n\n'
            "規則：\n"
            "1. fact 類只能來自 kind=price/onchain/regulatory 的文件；"
            "   social/news 的主張若無客觀數據支撐，一律標 inference 或 opinion。\n"
            "2. 每條主張獨立可評估，不合併多個主張。\n"
            "3. 只輸出 JSON array，不要其他說明文字。"
        )

        try:
            raw = self.complete(system=system_prompt, prompt=user_prompt)
            # 找到 JSON array（容錯：模型可能在 ``` 內）
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError("no JSON array in response")
            items: list[dict] = json.loads(raw[start:end])
        except Exception:
            # 解析失敗 → fallback regex，不崩潰
            return extract_claims(docs)

        claims: list[Claim] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            claim_text = str(item.get("claim", "")).strip()
            if not claim_text:
                continue
            claim_type = str(item.get("claim_type", "inference")).strip().lower()
            if claim_type not in {"fact", "inference", "opinion"}:
                claim_type = "inference"   # 非法值一律降為 inference(防 "fact " 繞過過濾)
            direction = str(item.get("direction", "neutral")).strip().lower()
            if direction not in {"bullish", "bearish", "neutral"}:
                direction = "neutral"
            src_doc_id = str(item.get("source_doc_id", "")).strip()

            # 來源對不上 → 跳過,不可掛到 docs[0](會把主張掛錯來源,污染信任分/溯源)
            doc = doc_map.get(src_doc_id)
            if doc is None:
                continue

            # 反作弊過濾：fact 只能來自客觀來源
            if claim_type == "fact" and doc.kind not in _OBJECTIVE_KINDS:
                claim_type = "inference"

            claims.append(Claim(
                id=f"{src_doc_id}#llm{i}",
                text=claim_text,
                doc=doc,
                claim_type=claim_type,
                direction=direction,
            ))

        # 若 LLM 什麼都沒抽到（空文件等），fallback regex
        return claims if claims else extract_claims(docs)
