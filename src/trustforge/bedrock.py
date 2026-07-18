"""AWS Bedrock runtime — TrustForge 唯一的模型入口。

競賽硬約束：僅限使用 AWS 服務提供之基礎模型。所有 LLM 呼叫都必須經過這裡，
不得直接呼叫其他供應商或集團內部電話總機（anemone）。集中於此方便合規審查與換模型。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ledger import estimate_cost

if TYPE_CHECKING:
    from .execlog import ExecutionLog
    from .ingestion.base import Document
    from .trust.scoring import Claim

# 僅客觀來源才能標記 claim_type=fact（反作弊：主觀社群/新聞不得宣稱是事實）
_OBJECTIVE_KINDS = frozenset({"price", "onchain", "regulatory"})


@dataclass
class LLMResult:
    """`BedrockClient.complete()` 的回傳型別：文字輸出 + token 用量（供成本記錄）。

    離線 / 未設 model_id 時 `input_tokens=output_tokens=0`、`model_id=None`
    （呼叫端據此估算成本恆為 $0，但仍可記一筆，讓帳本看得到「此 run 離線」）。
    """

    text: str
    input_tokens: int
    output_tokens: int
    model_id: str | None


# --- W1.5（#15）語意 stance 子分類器 ---------------------------------------
# 合法標籤：entailment（語意一致/相互支持）｜contradiction（明確方向對立）｜
# neutral（無明顯支持或衝突，含判斷不確定時的保守預設）。
_STANCE_LABELS = ("entailment", "contradiction", "neutral")
_STANCE_TOOL_NAME = "classify_stance"

# CEO/codex 對抗審修正：stance 呼叫走獨立、短 timeout 的 boto3 client（不與主敘事
# 模型的 self._runtime() 共用），避免 scoring.py 的 O(n²) 迴圈中單一慢呼叫拖垮整條、
# 吃光官方 15 分鐘執行窗口。分類任務 maxTokens=128（tool_use JSON 輸出需 >32，實測 32 會截斷成空回應→誤降 neutral）、應秒級回應，短 timeout 合理；
# 不重試（total_max_attempts=1，見下方 `_stance_runtime` docstring 對 botocore
# max_attempts vs total_max_attempts 語意差異的說明）讓逾時快速失敗進
# classify_stance() 的 except → "neutral"，不要讓 boto3 內建重試把等待時間再乘倍。
_STANCE_READ_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_STANCE_READ_TIMEOUT_SEC", "8"))
_STANCE_CONNECT_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_STANCE_CONNECT_TIMEOUT_SEC", "3"))

# #51 codex HIGH 複審 Round 14（/api/analyze single-flight dedup 的根因
# 修復）：`_runtime()`（主敘事模型呼叫，`complete()` 走這裡）先前**完全
# 沒有** timeout 設定——boto3 預設值等於「無限期等待」，代表 leader 若
# 卡在網路層/上游 hang 住，這條 server thread 會永久卡住，不會自己
# 超時失敗。這正是 `web.py` 的 `_dedup_analyze_call` 過去需要一整套
# 「偵測 stale leader 並取代」機制的根本原因——若 leader 呼叫本身有界，
# 就不需要在 dedup 層另外做取代/generation fencing 來补救一個「本來就不該
# 無限期 hang」的依賴呼叫。比照 `_stance_runtime()` 的作法加上明確
# `Config`，但敘事任務（含多輪 orchestrator 呼叫、較長 prompt）本身就比
# stance 分類慢得多，timeout 需要更寬鬆：預設 `read_timeout=60s`、
# `connect_timeout=10s`，仍短於前端 90 秒（見 `frontend/src/lib/apiClient.ts`）
# 疊加 dedup 層 `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS=45s` 的既有預算——
# 不會讓正常、未逾時的真實呼叫提前被打斷。`total_max_attempts=1`
# 理由同 `_stance_runtime()`：不重試，讓單次呼叫的最壞耗時有確定上界。
_NARRATIVE_READ_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_NARRATIVE_READ_TIMEOUT_SEC", "60"))
_NARRATIVE_CONNECT_TIMEOUT_SEC = int(os.getenv("TRUSTFORGE_NARRATIVE_CONNECT_TIMEOUT_SEC", "10"))

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
    # codex 審查發現的 HIGH：預設值必須跟預設 `stance_model_id` 用的 `au.` 地理
    # profile（只能從 ap-southeast-2/4/6 呼叫）相容——EC2/Bedrock 實際部署在
    # 雪梨，region 預設改 `ap-southeast-2`。舊預設 `us-east-1` 會讓沒設
    # `AWS_REGION` 環境變數的每一次 stance 呼叫都失敗（pipeline 降級 neutral、
    # gen 腳本中止），先前驗證時是因為手動帶了 `AWS_REGION=ap-southeast-2`
    # 才成功，掩蓋了這個預設不相容組合。env `AWS_REGION` 仍可覆寫。
    region: str = os.getenv("AWS_REGION", "ap-southeast-2")
    # 競賽現場（8/1）公告可用模型後填入；保持環境變數可配置，勿在程式碼寫死。
    # 目前預設空字串（未指定任何 region profile），故不受 region 相容性問題影響。
    model_id: str = os.getenv("BEDROCK_MODEL_ID", "")
    max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "1024"))
    # W1.5（#15）：語意 stance 子分類器專用小模型，與主敘事模型分開設定，
    # 讓高頻小任務可用更便宜/低延遲的模型，不佔用主模型預算。`au.` 地理 profile
    # 需搭配上面 `ap-southeast-2/4/6` 其中一個 region 才能呼叫。
    stance_model_id: str = os.getenv(
        "BEDROCK_HAIKU_MODEL_ID", "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    )


class BedrockClient:
    """薄封裝 boto3 bedrock-runtime 的 Messages API。

    離線模式（offline=True）不需 AWS 憑證，回傳佔位字串，方便在沒有 AWS 帳號時
    開發/測試信任層管線。
    """

    def __init__(
        self,
        config: BedrockConfig | None = None,
        offline: bool = False,
        stance_offline: bool | None = None,
    ):
        self.config = config or BedrockConfig()
        self.offline = offline
        # #9 online-stance 預算配額硬化：`stance_offline` 讓呼叫端能把「stance
        # 分類呼叫」的離線與否從主敘事 `offline` 解耦——未顯式傳入時預設等於
        # `offline`（向後相容，行為與加入這個參數前逐字相同）。唯一會傳入不同
        # 值的呼叫端是 `pipeline.run()`：公開預設 `data_mode=live,
        # llm_mode=off`（敘事離線）+ `TRUSTFORGE_ONLINE_STANCE` 開關生效時，
        # 敘事維持 offline=True 省下敘事成本，但 stance 判斷改用真 Bedrock
        # （`stance_offline=False`）。見 `budget_guard.online_stance_requested`。
        self.stance_offline = offline if stance_offline is None else stance_offline
        self._client = None
        self._stance_client = None  # W1.5：獨立、短 timeout，不與主敘事模型共用
        # 成本記錄用：classify_stance 在 scoring.py 的 O(n²) 迴圈深處被呼叫，
        # 呼叫端（cached_stance_fn/score()）無法方便地帶入 ExecutionLog，故改由
        # classify_stance 每次真呼叫（cache-miss）成功後自行把成本事件累積在此，
        # 呼叫端（orchestrator Step2）於 score() 完成後統一讀出並清空、寫回 log。
        self.cost_events: list[dict] = []

    def _runtime(self):
        """#51 codex HIGH 複審 Round 14：主敘事模型呼叫專用 client，見上方
        `_NARRATIVE_READ_TIMEOUT_SEC`/`_NARRATIVE_CONNECT_TIMEOUT_SEC` 常數
        註解——加上明確 `Config` 前，這裡沒有任何 timeout 上界（boto3
        預設等於無限期等待），是 dedup 層過去需要一整套 stale-leader 偵測/
        取代機制的根因；`total_max_attempts=1` 理由同 `_stance_runtime()`。
        """
        if self._client is None:
            import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.config.region,
                config=Config(
                    read_timeout=_NARRATIVE_READ_TIMEOUT_SEC,
                    connect_timeout=_NARRATIVE_CONNECT_TIMEOUT_SEC,
                    retries={"total_max_attempts": 1},
                ),
            )
        return self._client

    def _stance_runtime(self):
        """W1.5（#15）+ CEO/codex 對抗審修正：stance 分類專用的短 timeout client，
        見上方 `_STANCE_READ_TIMEOUT_SEC` 常數註解。

        retries 用 `total_max_attempts=1`（不是 `max_attempts=1`）：查過 botocore
        `Config` 的官方 docstring（`botocore/config.py`）——`max_attempts` 語意是
        「初始請求之外**還能重試幾次**」（`max_attempts=1` 代表初始 + 1 次重試 =
        共 2 次嘗試，等於把單次呼叫的最壞耗時翻倍）；`total_max_attempts` 才是
        「整個請求總共嘗試幾次（含初始請求）」，`total_max_attempts=1` 才是真正
        的「只打一次、不重試」，且兩者同時提供時 `total_max_attempts` 優先。這裡
        必須明確不重試，讓單次呼叫的最壞耗時有確定上界（見 scoring.py 的
        `STANCE_TIME_RESERVE_SEC`，需要跟這裡的 timeout 數字對得上才能保證
        15 分鐘官方執行窗口不被越界）。
        """
        if self._stance_client is None:
            import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS
            from botocore.config import Config

            self._stance_client = boto3.client(
                "bedrock-runtime",
                region_name=self.config.region,
                config=Config(
                    read_timeout=_STANCE_READ_TIMEOUT_SEC,
                    connect_timeout=_STANCE_CONNECT_TIMEOUT_SEC,
                    retries={"total_max_attempts": 1},
                ),
            )
        return self._stance_client

    def complete(self, system: str, prompt: str) -> LLMResult:
        """單輪文字生成。回傳 `LLMResult`（文字輸出 + token 用量，供成本記錄用）。

        離線模式：回傳佔位文字，token=0、model_id=None（呼叫端仍可記一筆 $0 成本，
        讓帳本看得到「此 run 離線」）。
        """
        if self.offline:
            text = f"[OFFLINE] (model={self.config.model_id or 'unset'}) would answer:\n{prompt[:280]}"
            return LLMResult(text=text, input_tokens=0, output_tokens=0, model_id=None)

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
        text = "".join(b.get("text", "") for b in payload.get("content", []))
        usage = payload.get("usage", {}) or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            model_id=self.config.model_id,
        )

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

        ⚠️ 此降級行為只適合「pipeline 即時打分」場景（單點故障不能拖垮整條管線）。
        離線批次生成快取（`scripts/gen_stance_cache.py`）**不可**用這個方法——會把
        「呼叫失敗」跟「模型真的判斷 neutral」混為一談，把假 neutral 悄悄寫進
        持久化快取、弱化矛盾偵測。批次生成請改用 `classify_stance_strict()`。

        判斷用 `self.stance_offline`（非 `self.offline`）——#9 online-stance
        預算配額硬化：兩者預設相同（見 `__init__`），只有 `pipeline.run()`
        在「敘事離線但 online-stance 開關生效」時才會讓兩者不同值。
        """
        if self.stance_offline or not self.config.stance_model_id:
            return "neutral"
        try:
            return self._classify_stance_impl(a, b)
        except Exception:
            # 任何失敗（憑證/逾時/回應格式不符）一律保守回 neutral，不 raise
            return "neutral"

    def classify_stance_strict(self, a: str, b: str) -> str:
        """`classify_stance` 的嚴格版：**任何失敗一律 raise，不回 neutral**。

        供離線批次生成持久化快取（`scripts/gen_stance_cache.py`）使用——那個場景
        「呼叫失敗」跟「模型真的判斷 neutral」必須明確分開，否則失敗會被悄悄寫成
        看似合法的 neutral entry，污染 `stance_cache.json`、弱化矛盾偵測（見 codex
        審查發現的 HIGH）。離線模式、未設 stance_model_id、逾時、憑證錯誤、回應格式
        不符、或回應內容缺少合法 toolUse.label，一律 raise，讓呼叫端（gen 腳本）
        中止且不寫檔。

        ⚠️ 不供 pipeline 即時打分使用——pipeline 需要 `classify_stance` 的降級容錯，
        避免單一逾時拖垮整條 O(n²) 迴圈。
        """
        if self.offline:
            raise RuntimeError(
                "classify_stance_strict: client 為 offline 模式，無法產生真實分類"
                "（離線模式本就無法呼叫 Bedrock，故意 raise 避免呼叫端誤把佔位結果當真）"
            )
        if not self.config.stance_model_id:
            raise RuntimeError("classify_stance_strict: stance_model_id 未設定")
        return self._classify_stance_impl(a, b)

    def _classify_stance_impl(self, a: str, b: str) -> str:
        """`classify_stance` / `classify_stance_strict` 共用的核心呼叫/解析邏輯。

        任何失敗（逾時、憑證錯誤、回應格式不符、缺少合法 toolUse.label）一律
        raise；是否要吞成 "neutral" 由呼叫端（`classify_stance` 的 try/except）
        決定，這裡不做降級判斷。
        """
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

        resp = self._stance_runtime().converse(
            modelId=self.config.stance_model_id,
            system=[{"text": _STANCE_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 128},
            toolConfig=tool_config,
        )
        # 只在真呼叫（converse 已成功回來）才記成本——
        # cache-hit 完全不會走到這個函式（見 stance_cache.cached_stance_fn）。
        usage = resp.get("usage", {}) or {}
        tokens_in = int(usage.get("inputTokens", 0) or 0)
        tokens_out = int(usage.get("outputTokens", 0) or 0)
        self.cost_events.append({
            "model": self.config.stance_model_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": estimate_cost(self.config.stance_model_id, tokens_in, tokens_out),
        })
        blocks = resp["output"]["message"]["content"]
        for block in blocks:
            tool_use = block.get("toolUse")
            if tool_use and tool_use.get("name") == _STANCE_TOOL_NAME:
                label = str(tool_use.get("input", {}).get("label", "")).strip().lower()
                if label in _STANCE_LABELS:
                    return label
        raise ValueError("classify_stance: 回應內容缺少合法的 toolUse.label")

    # ------------------------------------------------------------------
    # Step 1: LLM-based Claim 抽取（Bedrock 呼叫 #1）
    # ------------------------------------------------------------------
    def extract_claims_with_llm(
        self, docs: list[Document], log: "ExecutionLog | None" = None
    ) -> list[Claim]:
        """從多源 Document 用 LLM 抽出結構化主張。

        離線模式或未設模型時 fall back 回 regex extract_claims，確保測試/離線 demo 不變。
        線上模式：Bedrock 呼叫 #1，輸出 claim_type + direction；
                  fact 類只來自客觀來源（price/onchain/regulatory），事後過濾。

        `log`：可選的 `ExecutionLog`，真呼叫（非 fallback）成功後把 token 用量／
        估算成本記一筆 `llm.cost`（見 `ExecutionLog.record_llm_cost`）。
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
            result = self.complete(system=system_prompt, prompt=user_prompt)
            if log is not None:
                # 真呼叫已成功取得 usage → 記一筆成本，不管後面 JSON 解析是否成功
                # （呼叫本身已發生、已有花費，parse 失敗只是降級 fallback，不代表沒呼叫）
                log.record_llm_cost(
                    result.model_id,
                    result.input_tokens,
                    result.output_tokens,
                    estimate_cost(result.model_id, result.input_tokens, result.output_tokens),
                )
            raw = result.text
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
