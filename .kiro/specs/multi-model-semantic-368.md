# Spec: 多模型語意分析架構 (#368)

> Issue: #367, #368
> Priority: P0-critical
> Plan: `docs/plans/PLAN-multi-model-semantic-analysis-368.md`

---

## 問題陳述

1. 校準模型（`data/model-artifacts/calibration-model.json`）用全中性舊資料訓練，把所有 raw confidence 壓低 → 永遠棄權
2. 方向判定依賴 regex 關鍵字（`_infer_direction`），非語意理解，不穩定
3. 缺乏多模型/多視角的獨立判斷與加權合併機制

## Requirements

### R1: 移除壞校準模型（Phase 1 — 緊急）
- 刪除 `data/model-artifacts/calibration-model.json`
- `_calibrate_confidence()` fallback 到 `_CALIBRATION_TABLE`
- 驗證 raw 0.5 映射後 ≥ 0.45

### R2: 棄權時仍給方向（Phase 1 — 緊急）
- `is_abstain=True` 時仍呼叫 `_direction()` 取得方向
- 報告標注「僅供參考，非投資建議」
- direction 不再硬設「不明」

### R3: LLM 語意方向分析（Phase 2）
- 建立 3 組 prompt：price / news / onchain
- 用 Bedrock Converse API `tool_use` 強制結構化輸出
- 輸出 schema：`{direction: bullish|bearish|neutral, confidence: float, reasoning: str}`
- 每次呼叫 ≤ 8s timeout
- parse 失敗時 graceful fallback 到 regex

### R4: 多模型加權合併（Phase 3）
- 5 個 voter：OHLCV 統計 / LLM-price / LLM-news / LLM-onchain / stance consensus
- 各 voter 獨立產出 `{direction, confidence}`
- Dawid-Skene EM 估算 voter 信譽 `r(voter)`
- 加權多數決：`final_direction = argmax(Σ r_i × conf_i × I[vote_i == label])`
- 一致性 confidence：`max_label_weight / total_weight`

### R5: 確定性 fallback
- LLM 不可用時 fallback 到 OHLCV 統計（Layer 1）
- 離線模式不呼叫 Bedrock
- voter 數 < 3 時 DS 退化為等權多數決

### R6: 競賽合規
- 所有 Bedrock 呼叫經 `bedrock.py`
- 語意分析 = 分類工具，不替代 pipeline 判斷（反作弊）
- 15 分鐘內完成全流程
- 每個方向結論帶溯源鏈

---

## Design

### 新增檔案

```
src/trustforge/trust/
├── semantic_prompts.py      # 3 組 prompt 模板
├── semantic_analyzer.py     # Bedrock Converse API 語意分析
└── multi_model_voter.py     # 多模型加權合併（Phase 3）
```

### 介面定義

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

### Converse API tool_use

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

### 修改 orchestrator.py `_direction()` 整合

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

---

## Tasks

### Phase 1（7/21 today）
- [ ] `git rm data/model-artifacts/calibration-model.json`
- [ ] 修改 `build_report` 棄權邏輯：棄權時仍呼叫 `_direction()`
- [ ] 親測 5 幣有方向
- [ ] pytest 全通過

### Phase 2（7/22–7/25）
- [ ] 建立 `semantic_prompts.py`（3 組 prompt）
- [ ] 建立 `semantic_analyzer.py`（Converse API + tool_use）
- [ ] 快取機制（SQLite 24h）
- [ ] timeout + fallback 到 regex
- [ ] 測試：mock Bedrock、parse、快取、降級
- [ ] feature flag `SEMANTIC_DIRECTION_ENABLED`

### Phase 3（7/28–7/31）
- [ ] 建立 `multi_model_voter.py`
- [ ] 整合 Dawid-Skene 加權
- [ ] 替換 `_direction()` 主路徑
- [ ] 降級策略（voter < 2）
- [ ] 測試：一致/分歧/降級/單 voter
- [ ] 移除 feature flag，語意分析成為預設

---

## Acceptance Criteria（全計劃完成後）

1. ✅ 壞校準模型已移除，不再影響 confidence
2. ✅ 棄權時仍有方向趨勢（≥4/5 幣非「不明」）
3. ✅ 語意分析可正確 parse Bedrock tool_use 回應
4. ✅ 多模型加權 5 幣全有方向（正常模式 0 個「不明」）
5. ✅ 多源一致 → confidence ≥ 0.70；分歧 → confidence ≤ 0.50
6. ✅ 任何 voter 失敗時 pipeline 不崩（graceful degradation）
7. ✅ 全流程 ≤ 15 分鐘
8. ✅ 離線模式不呼叫 Bedrock
9. ✅ 所有 LLM 呼叫經 `bedrock.py`（競賽合規）
10. ✅ pytest 通過，新增測試覆蓋 3 個新模組

---

## Dependencies

- `src/trustforge/bedrock.py` — Converse API 已支援 tool_use ✅
- `src/trustforge/trust/dawid_skene.py` — EM 估計器已實作 ✅
- `src/trustforge/trust/stance_cache.py` — SQLite 快取模式可複用 ✅
- HOYA BIT OHLCV 資料 — `data/` 目錄已就位 ✅
