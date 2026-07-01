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

# --- W1.5（#15）語意 stance 子分類器 ---------------------------------------
# 合法標籤：entailment（語意一致/相互支持）｜contradiction（明確方向對立）｜
# neutral（無明顯支持或衝突，含判斷不確定時的保守預設）。
_STANCE_LABELS = ("entailment", "contradiction", "neutral")
_STANCE_TOOL_NAME = "classify_stance"

# CEO/codex 對抗審修正：stance 呼叫走獨立、短 timeout 的 boto3 client（不與主敘事
# 模型的 self._runtime() 共用），避免 scoring.py 的 O(n²) 迴圈中單一慢呼叫拖垮整條、
# 吃光官方 15 分鐘執行窗口。分類任務 maxTokens=32、應秒級回應，短 timeout 合理；
# 不重試（max_attempts=1）讓逾時快速失敗進 classify_stance() 的 except → "neutral"，
# 不要讓 boto3 內建重試把等待時間再乘倍。
_STANCE_READ_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_STANCE_READ_TIMEOUT_SEC", "8"))
_STANCE_CONNECT_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_STANCE_CONNECT_TIMEOUT_SEC", "3"))

_STANCE_SYSTEM = (
    "你是金融/監管文本的語意立場分類器。給定兩句市場相關敘述 A、B，判斷 B 相對 A 的立場，"
    "只能三選一：\n"
    "- entailment：B 與 A 語意一致、方向相容，或只是換句話說（即使 B 帶有 caution/"
    "precautionary/謹慎 等審慎措辭，只要沒有明確提出相反主張，仍算 entailment）。\n"
    "- contradiction：B 與 A 在市場方向或立場上明確對立、互斥（例如一方主張『明朗化/"
    "將推動採用』，另一方主張『收緊/呼籲審慎抵制』）。\n"
    "- neutral：兩者話題相關但無明顯支持或衝突關係，或你無法確定。\n"
    "判不準時一律回 neutral（寧可漏抓，不可誤判 contradiction 錯殺合法佐證）。"
)

# few-shot：取自前幾輪 code review 的真實案例，避免詞面重疊被誤判、也避免漏抓真矛盾。
_STANCE_FEWSHOT = [
    # review#1：despite caution 語境仍是同向支撐，不是矛盾
    {
        "a": "Institutional adoption continues rising despite short-term regulatory caution.",
        "b": "Institutional adoption continues rising steadily this quarter.",
        "label": "entailment",
    },
    # review#2：precautionary framework 不等於「反對採用」
    {
        "a": "Adoption continues under a precautionary regulatory framework.",
        "b": "Adoption is rising steadily.",
        "label": "entailment",
    },
    # Issue #15 核心案例：clarity/adoption vs scrutiny/caution → 明確對立
    {
        "a": "Market analysts expect regulatory clarity to boost institutional adoption significantly.",
        "b": "Market observers expect regulatory scrutiny to boost investor caution significantly.",
        "label": "contradiction",
    },
]


@dataclass
class BedrockConfig:
    region: str = os.getenv("AWS_REGION", "us-east-1")
    # 競賽現場（8/1）公告可用模型後填入；保持環境變數可配置，勿在程式碼寫死。
    model_id: str = os.getenv("BEDROCK_MODEL_ID", "")
    max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "1024"))
    # W1.5（#15）：語意 stance 子分類器專用小模型，與主敘事模型分開設定，
    # 讓高頻小任務可用更便宜/低延遲的模型，不佔用主模型預算。
    stance_model_id: str = os.getenv(
        "BEDROCK_HAIKU_MODEL_ID", "apac.anthropic.claude-haiku-4-5"
    )


class BedrockClient:
    """薄封裝 boto3 bedrock-runtime 的 Messages API。

    離線模式（offline=True）不需 AWS 憑證，回傳佔位字串，方便在沒有 AWS 帳號時
    開發/測試信任層管線。
    """

    def __init__(self, config: BedrockConfig | None = None, offline: bool = False):
        self.config = config or BedrockConfig()
        self.offline = offline
        self._client = None
        self._stance_client = None  # W1.5：獨立、短 timeout，不與主敘事模型共用

    def _runtime(self):
        if self._client is None:
            import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS

            self._client = boto3.client("bedrock-runtime", region_name=self.config.region)
        return self._client

    def _stance_runtime(self):
        """W1.5（#15）+ CEO/codex 對抗審修正：stance 分類專用的短 timeout client，
        見上方 `_STANCE_READ_TIMEOUT_SEC` 常數註解。"""
        if self._stance_client is None:
            import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS
            from botocore.config import Config

            self._stance_client = boto3.client(
                "bedrock-runtime",
                region_name=self.config.region,
                config=Config(
                    read_timeout=_STANCE_READ_TIMEOUT_SEC,
                    connect_timeout=_STANCE_CONNECT_TIMEOUT_SEC,
                    retries={"max_attempts": 1},
                ),
            )
        return self._stance_client

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
    # W1.5（#15）：語意 stance 子分類器（Bedrock Converse API + 強制 tool-use）
    # ------------------------------------------------------------------
    def classify_stance(self, a: str, b: str) -> str:
        """判斷 b 相對 a 的語意立場："entailment" | "contradiction" | "neutral"。

        用 Converse API 的 toolConfig 強制模型只能從三個合法標籤中選一個（enum 結構化
        輸出），temperature=0 求最大確定性，避免自由文字輸出需要額外解析、也避免模型
        講出詞表外的答案。

        離線模式、未設 stance_model_id、或呼叫/解析過程任何失敗（含逾時）→ 一律回
        "neutral"，不 raise、不中斷管線（比照 extract_claims_with_llm 的降級哲學：
        寧可漏抓真矛盾，也不可讓 stance 分類器變成單點故障）。
        """
        if self.offline or not self.config.stance_model_id:
            return "neutral"

        fewshot_block = "\n".join(
            f"範例{i}：A=「{ex['a']}」 B=「{ex['b']}」 → {ex['label']}"
            for i, ex in enumerate(_STANCE_FEWSHOT, start=1)
        )
        user_text = (
            f"{fewshot_block}\n\n"
            f"A=「{a}」\nB=「{b}」\n"
            f"請呼叫 {_STANCE_TOOL_NAME} 工具，回傳 B 相對 A 的立場。"
        )
        tool_config = {
            "tools": [
                {
                    "toolSpec": {
                        "name": _STANCE_TOOL_NAME,
                        "description": "回傳兩句市場敘述之間的語意立場分類。",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "enum": list(_STANCE_LABELS),
                                    }
                                },
                                "required": ["label"],
                            }
                        },
                    }
                }
            ],
            "toolChoice": {"tool": {"name": _STANCE_TOOL_NAME}},
        }

        try:
            resp = self._stance_runtime().converse(
                modelId=self.config.stance_model_id,
                system=[{"text": _STANCE_SYSTEM}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"temperature": 0, "maxTokens": 32},
                toolConfig=tool_config,
            )
            blocks = resp["output"]["message"]["content"]
            for block in blocks:
                tool_use = block.get("toolUse")
                if tool_use and tool_use.get("name") == _STANCE_TOOL_NAME:
                    label = str(tool_use.get("input", {}).get("label", "")).strip().lower()
                    if label in _STANCE_LABELS:
                        return label
            return "neutral"
        except Exception:
            # 任何失敗（憑證/逾時/回應格式不符）一律保守回 neutral，不 raise
            return "neutral"

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
