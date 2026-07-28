# Multi-angle Synthesis 實作任務

## Tasks

- [ ] 1. 建立 `src/trustforge/multi_angle.py` 骨架，定義 AngleResult / AngleConflict / MultiAngleReport dataclass
- [ ] 2. 實作 `angle_result_from_payload(mode, payload_json)` 反序列化函式（含容錯）
- [ ] 3. 實作 `_is_opposing()` 與 `_weighted_confidence()` 工具函式
- [ ] 4. 實作 `synthesize_angles()` Phase 1-3：分類 active/abstained、全 abstain 快速路徑、方向統計
- [ ] 5. 實作 `synthesize_angles()` Phase 4：衝突偵測（direction_divergence + confidence_gap）
- [ ] 6. 實作 `synthesize_angles()` Phase 5-6：證據獨立性評估 + 共識推導
- [ ] 7. 實作 `synthesize_angles()` Phase 7-8：agreement_matrix + 報告組裝 + synthesis_summary 模板
- [ ] 8. 建立 `tests/test_multi_angle.py`：全 normal 同方向、方向背離、單 abstain、全 abstain、證據重疊、信心差距、混合情境
- [ ] 9. 確認所有測試通過、lint 通過、無新依賴引入
