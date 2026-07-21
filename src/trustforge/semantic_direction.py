"""多來源語意方向分析：用 LLM 對不同類型的證據做方向判斷。

Phase 2 of Issue #368：用 Bedrock LLM 做語意方向判斷，取代關鍵字/數值門檻。
每個來源類型有專屬 prompt，輸出結構化 {direction, confidence, reasoning}。

設計原則：
- LLM 失敗 = 沒投票（不崩），graceful degradation
- 時間預算：限制最多 3 次呼叫（price + news + onchain），每次 timeout 10s
- 不引入新依賴（純 stdlib + boto3）
- 只在非 offline 時才呼叫 LLM
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class DirectionVote:
    """單一來源類型的方向投票結果。"""

    source_type: str   # "price" | "news" | "onchain" | "sentiment"
    direction: str     # "bullish" | "bearish" | "neutral"
    confidence: float  # 0-1
    reasoning: str


# --- 各來源類型專屬 prompt ---------------------------------------------------

PRICE_PROMPT = """你是加密貨幣價格分析師。根據以下 OHLCV 價格數據，判斷短期（7 天）趨勢方向。
只能回答 bullish/bearish/neutral 其中一個，並給出 0-1 的信心度。

數據：
{evidence_text}
"""

NEWS_PROMPT = """你是加密貨幣新聞分析師。根據以下新聞摘要，判斷市場情緒方向。
只能回答 bullish/bearish/neutral 其中一個，並給出 0-1 的信心度。

新聞：
{evidence_text}
"""

ONCHAIN_PROMPT = """你是區塊鏈分析師。根據以下鏈上指標，判斷資金流向。
只能回答 bullish/bearish/neutral 其中一個，並給出 0-1 的信心度。

指標：
{evidence_text}
"""

SENTIMENT_PROMPT = """你是市場情緒分析師。根據以下情緒指標，判斷市場情緒方向。
只能回答 bullish/bearish/neutral 其中一個，並給出 0-1 的信心度。

指標：
{evidence_text}
"""

SOURCE_TYPE_PROMPTS: dict[str, str] = {
    "price": PRICE_PROMPT,
    "news": NEWS_PROMPT,
    "onchain": ONCHAIN_PROMPT,
    "sentiment": SENTIMENT_PROMPT,
}

# 系統提示：強制 JSON 輸出格式
_SYSTEM_PROMPT = (
    '你是加密市場分析 AI。回答格式必須是 JSON: '
    '{"direction": "bullish|bearish|neutral", "confidence": 0.0-1.0, "reasoning": "..."}'
)

# 時間預算：最多處理的來源類型數（避免多次 LLM 呼叫吃光 15 分鐘窗口）
_MAX_SOURCE_TYPES = 3
# 優先處理順序（price 最客觀、news 次之、onchain 第三）
_PRIORITY_ORDER = ("price", "news", "onchain", "sentiment")


def analyze_direction(
    evidence_by_type: dict[str, list[str]],
    client,
) -> list[DirectionVote]:
    """對每個來源類型用專屬 prompt 呼叫 LLM，回傳投票列表。

    Parameters
    ----------
    client : BedrockClient instance（需有 .complete(system, prompt) 方法）
    evidence_by_type : {"price": ["text1", ...], "news": [...], ...}

    Returns
    -------
    list[DirectionVote]：每個成功解析的來源類型產生一票。
        LLM 失敗或解析失敗的來源類型不產生投票（graceful degradation）。

    Notes
    -----
    - 最多呼叫 _MAX_SOURCE_TYPES 次 LLM（預設 3），按 _PRIORITY_ORDER 排序
    - 每個來源類型最多餵 10 條文本給 LLM（控制 token 用量）
    - client.offline == True 時一律回空列表（不呼叫 LLM）
    """
    # 離線模式不呼叫 LLM
    if getattr(client, "offline", False):
        return []

    votes: list[DirectionVote] = []
    call_count = 0

    # 按優先順序處理，最多 _MAX_SOURCE_TYPES 次呼叫
    for source_type in _PRIORITY_ORDER:
        if call_count >= _MAX_SOURCE_TYPES:
            break
        texts = evidence_by_type.get(source_type)
        if not texts or source_type not in SOURCE_TYPE_PROMPTS:
            continue

        prompt_template = SOURCE_TYPE_PROMPTS[source_type]
        evidence_text = "\n".join(texts[:10])  # 限制長度
        prompt = prompt_template.format(evidence_text=evidence_text)

        try:
            result = client.complete(
                system=_SYSTEM_PROMPT,
                prompt=prompt,
            )
            call_count += 1
            parsed = _parse_llm_response(result.text)
            if parsed:
                votes.append(DirectionVote(
                    source_type=source_type,
                    direction=parsed["direction"],
                    confidence=parsed["confidence"],
                    reasoning=parsed["reasoning"],
                ))
        except Exception:
            # LLM 失敗不崩，該來源沒有投票
            call_count += 1  # 仍計入呼叫次數（避免無限重試）

    return votes


def _parse_llm_response(text: str) -> dict | None:
    """解析 LLM 回應的 JSON。

    嘗試策略：
    1. 直接 json.loads 整段文字
    2. 用 regex 提取第一個 {...} 再解析

    回傳 {"direction", "confidence", "reasoning"} 或 None（解析失敗）。
    """
    # 策略 1：直接解析
    try:
        d = json.loads(text)
        if _is_valid_direction_response(d):
            return _normalize_response(d)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 策略 2：regex 提取 JSON object
    # 使用非貪婪匹配，處理可能包含嵌套的情況
    m = re.search(r'\{[^{}]*\}', text)
    if m:
        try:
            d = json.loads(m.group())
            if _is_valid_direction_response(d):
                return _normalize_response(d)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    return None


def _is_valid_direction_response(d: object) -> bool:
    """檢查解析結果是否為合法的方向回應。"""
    if not isinstance(d, dict):
        return False
    return d.get("direction") in ("bullish", "bearish", "neutral")


def _normalize_response(d: dict) -> dict:
    """正規化解析結果。"""
    confidence = d.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        confidence = 0.5
    # clamp to [0, 1]
    confidence = max(0.0, min(1.0, confidence))
    return {
        "direction": d["direction"],
        "confidence": confidence,
        "reasoning": str(d.get("reasoning", "")),
    }


def aggregate_votes(votes: list[DirectionVote]) -> tuple[str, float]:
    """用信心度加權合併多來源投票。回傳 (direction, confidence)。

    判定規則：
    - bullish 加權 > bearish × 1.3 且 > neutral → "bullish"
    - bearish 加權 > bullish × 1.3 且 > neutral → "bearish"
    - 否則 → "neutral"

    confidence 為勝出方向的加權佔比（0-1）。

    Parameters
    ----------
    votes : list[DirectionVote]
        各來源類型的投票結果

    Returns
    -------
    tuple[str, float]
        (direction, confidence)。空投票回傳 ("neutral", 0.0)。
    """
    if not votes:
        return "neutral", 0.0

    bullish_w = sum(v.confidence for v in votes if v.direction == "bullish")
    bearish_w = sum(v.confidence for v in votes if v.direction == "bearish")
    neutral_w = sum(v.confidence for v in votes if v.direction == "neutral")

    total = bullish_w + bearish_w + neutral_w
    if total == 0:
        return "neutral", 0.0

    if bullish_w > bearish_w * 1.3 and bullish_w > neutral_w:
        return "bullish", bullish_w / total
    elif bearish_w > bullish_w * 1.3 and bearish_w > neutral_w:
        return "bearish", bearish_w / total
    else:
        return "neutral", neutral_w / total
