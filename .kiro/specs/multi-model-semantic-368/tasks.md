# Tasks

## Phase 1（7/21 today）
- [ ] `git rm data/model-artifacts/calibration-model.json`
- [ ] 修改 `build_report` 棄權邏輯：棄權時仍呼叫 `_direction()`
- [ ] 親測 5 幣有方向
- [ ] pytest 全通過

## Phase 2（7/22–7/25）
- [ ] 建立 `semantic_prompts.py`（3 組 prompt）
- [ ] 建立 `semantic_analyzer.py`（Converse API + tool_use）
- [ ] 快取機制（SQLite 24h）
- [ ] timeout + fallback 到 regex
- [ ] 測試：mock Bedrock、parse、快取、降級
- [ ] feature flag `SEMANTIC_DIRECTION_ENABLED`

## Phase 3（7/28–7/31）
- [ ] 建立 `multi_model_voter.py`
- [ ] 整合 Dawid-Skene 加權
- [ ] 替換 `_direction()` 主路徑
- [ ] 降級策略（voter < 2）
- [ ] 測試：一致/分歧/降級/單 voter
- [ ] 移除 feature flag，語意分析成為預設
