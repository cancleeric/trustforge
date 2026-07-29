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
