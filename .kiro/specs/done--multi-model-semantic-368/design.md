# Design

## 新增檔案

```
src/trustforge/trust/
├── semantic_prompts.py      # 3 組 prompt 模板
├── semantic_analyzer.py     # Bedrock Converse API 語意分析
└── multi_model_voter.py     # 多模型加權合併（Phase 3）
```

## 介面定義

```python
# semantic_analyzer.py
@dataclass
class SemanticDirection:
    direction: str         # "bullish" | "bearish" | "neutral"
    confidence: float      # 0.0 - 1.0
    reasoning: str         # ≤ 200 chars
    source_type: str       # "price" | "news" | "onchain"
    voter_id: str          # 唯一識別

async def analyze_direction(
    coin: str,
    source_type: str,
    data: str,
    *,
    timeout_sec: float = 8.0,
    offline: bool = False,
) -> SemanticDirection | None:
    """呼叫 Bedrock 語意分析，回傳結構化方向；失敗回 None。"""

# multi_model_voter.py
@dataclass
class VoteResult:
    direction: str
    confidence: float
    voter_id: str
    reputation: float      # DS 估出的信譽

def aggregate_direction(
    votes: list[VoteResult],
    history: list[dict] | None = None,
) -> tuple[str, float]:
    """加權多數決，回傳 (final_direction, final_confidence)。"""
```

## Converse API tool_use

```python
DIRECTION_TOOL = {
    "name": "report_direction",
    "description": "報告方向分析結果",
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "maxLength": 200}
        },
        "required": ["direction", "confidence", "reasoning"]
    }
}
# toolChoice: {"tool": {"name": "report_direction"}} → 強制呼叫
```

## 修改 orchestrator.py `_direction()` 整合

```python
def _direction(supporting, all_scored=None, *, semantic_results=None):
    """多模型加權方向判定。

    Phase 3 路徑（semantic_results 有值時）：
      → multi_model_voter.aggregate_direction(...)

    Phase 1/2 路徑（fallback）：
      → Layer 1 OHLCV → Layer 2 stance → "不明"
    """
    if semantic_results and len(semantic_results) >= 2:
        votes = [VoteResult(...) for r in semantic_results]
        return aggregate_direction(votes)

    # Legacy path
    price_dir = _price_trend_direction(supporting, all_scored=all_scored)
    if price_dir and price_dir != "中性":
        return price_dir
    stance_dir = _stance_consensus_direction(supporting)
    if stance_dir:
        return stance_dir
    return price_dir or "不明"
```
