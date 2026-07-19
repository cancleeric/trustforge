"""TrustForge AgentCore Runtime 入口。

將既有的 pipeline.run() 包成 Strands tool，透過 BedrockAgentCoreApp 提供服務。
LLM 推理由 Strands Agent 負責對話編排，pipeline 內部的 Bedrock 呼叫維持不變。
"""
import sys
from pathlib import Path

# 讓 AgentCore CodeZip 打包後仍能 import src/trustforge
_SRC_PATH = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from memory.session import get_memory_session_manager

app = BedrockAgentCoreApp()
log = app.logger

# --- System Prompt ---

SYSTEM_PROMPT = """\
你是 TrustForge Hermes — 加密市場信任提煉分析師。

你的核心能力：
- 對多源資訊（新聞、社群、鏈上、HOYA BIT 行情、監管公告）做信任提煉
- 每條主張都有可信度評分（來源信譽 + 交叉佐證 + 時效衰減）
- 輸出帶溯源鏈的分析報告，讓使用者能查證

使用規則：
- 有分析需求時，呼叫 analyze_market tool
- 如果使用者只是閒聊或問非加密相關問題，直接回答即可
- 回答時永遠標注資訊來源和信任分數
- 使用繁體中文回答
"""

# --- Tools ---


@tool
def analyze_market(
    coin: str,
    query: str,
    question_type: str = "multi_source",
    data_mode: str = "live",
    llm_mode: str = "off",
) -> str:
    """執行 TrustForge 信任提煉分析管線。

    Args:
        coin: 幣種代碼，如 BTC, ETH, SOL, DOGE 等
        query: 使用者的分析問題（繁中或英文皆可）
        question_type: 問題類型 — multi_source | single_source | comparison
        data_mode: 資料來源模式 — live（真實連接器）| sample（離線樣本，$0）
        llm_mode: LLM 模式 — bedrock（真 Bedrock 推理）| off（免 LLM，$0）

    Returns:
        JSON 格式的分析報告，含信任分數、溯源鏈、反方證據
    """
    from trustforge.pipeline import run
    from trustforge.schema import QuestionType

    try:
        qtype = QuestionType(question_type)
    except ValueError:
        qtype = QuestionType.MULTI_SOURCE

    report, evidence, exec_log = run(
        coin=coin,
        query=query,
        qtype=qtype,
        data_mode=data_mode,
        llm_mode=llm_mode,
    )

    # 組裝回傳摘要
    result_parts = [
        f"# 分析報告: {coin.upper()}",
        f"問題: {query}",
        f"信任分數: {report.confidence:.2f}",
        f"信任等級: {report.confidence_label}",
        f"市場判斷: {report.market_judgment}",
        f"資料模式: {data_mode} | LLM: {llm_mode}",
        "",
        "## 關鍵事實",
    ]
    for fact in (report.facts or [])[:5]:
        result_parts.append(f"  - {fact}")

    result_parts.append("")
    result_parts.append(f"## 證據來源 ({len(evidence)} 筆)")

    for i, ev in enumerate(evidence[:10], 1):
        source = getattr(ev, 'source', 'unknown') or 'unknown'
        trust = getattr(ev, 'trust_score', 0) or 0
        text = (getattr(ev, 'text', str(ev)) or str(ev))[:100]
        result_parts.append(f"  {i}. [{source}] (信任: {trust:.2f}) {text}")

    if report.cross_source_signal:
        result_parts.append("")
        result_parts.append(f"## 跨來源分歧: {report.cross_source_signal}")

    return "\n".join(result_parts)


@tool
def list_supported_coins() -> str:
    """列出 TrustForge 目前支援分析的幣種。

    Returns:
        支援的幣種清單
    """
    from trustforge.schema import COIN_POOL

    return f"支援的幣種：{', '.join(sorted(COIN_POOL))}"


# --- Agent Setup ---

tools = [analyze_market, list_supported_coins]


@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking TrustForge Agent...")

    # 每次 request 建新 agent，避免前一輪對話的空 content 污染 messages history
    agent = Agent(
        model=load_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
    )

    prompt = payload.get("prompt", "hello")
    if not prompt or not prompt.strip():
        prompt = "hello"

    stream = agent.stream_async(prompt)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]


if __name__ == "__main__":
    app.run()
