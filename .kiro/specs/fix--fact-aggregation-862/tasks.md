# 實作任務：#862 退件修正

## Task 1: evidence_grouper 來源正規化修正（FR-3）

- [ ] `_normalize_source()` 改為呼叫 `canonical_source()` from `trustforge_core.source_identity`
- [ ] 確認 import 路徑正確、不產生循環依賴
- [ ] 既有 test_evidence_grouper.py 全數通過

## Task 2: evidence_grouper direction 隔離（FR-1）

- [ ] 新增 `_direction_bucket(ev: Evidence) -> str` 函式
- [ ] `group_evidence()` 分桶 key 改為 `(normalized_source, kind, direction_bucket)`
- [ ] independent set 判定不受 direction 影響（flagged 仍獨立）
- [ ] 既有測試通過；全覆蓋不變式仍成立

## Task 3: evidence_grouper 單位一致性檢查（FR-2）

- [ ] `_finalize_group()` 在計算數值前收集所有 unit（lowercase 正規化）
- [ ] `len(units_seen) > 1` 時設 trend=None, value_range=None, latest_value=None
- [ ] 群組本身仍成立（member_indices 不受影響）

## Task 4: orchestrator key_basis 前三面向多樣性（FR-4）

- [ ] 修改 `build_report()` 中 key_basis 去重邏輯
- [ ] 前 3 條 BasisItem 強制 `(source, kind)` 互不相同（不足 3 種面向時取盡可能）
- [ ] 第 4 條起允許重複面向（但仍受群組去重約束）

## Task 5: 邊界測試

- [ ] 新增 `tests/test_evidence_grouper_fix862.py`
- [ ] test: bullish + bearish 同 source/kind → 不聚合為同組
- [ ] test: 同 metric 不同 unit → value_range=None, trend=None
- [ ] test: coindesk.com + coindesk → 聚合為同組（canonical alias）
- [ ] test: key_basis 前 3 條 (source, kind) 互不相同
- [ ] test: direction 加入後全覆蓋不變式仍成立

## Task 6: 回歸驗證

- [ ] pytest 全套通過
- [ ] 前端 vitest 通過
- [ ] lint / type-check 通過
