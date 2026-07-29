# 實作任務：非破壞式事實聚合與介面呈現優化

> Issue: #862

## Task 1: 建立 evidence_grouper.py 模組骨架

- [x] 建立 `src/trustforge/agent/evidence_grouper.py`
- [x] 實作 `EvidenceGroup` dataclass
- [x] 實作 `_extract_metric_key(content_reference: str) -> str | None`
  - 正則匹配中英文指標名稱（如「算力」「Gas Fee」「price」）
  - 支援 `"指標: 值"` / `"指標 = 值"` / `"指標：值"` 格式
- [x] 實作 `_extract_numeric_value(content_reference: str) -> tuple[float, str] | None`
  - 提取數值（支援逗號分隔）與單位
- [x] 實作 `_compute_trend(values: list[tuple[float, float]]) -> str | None`
  - 2% 門檻判定 rising/falling/stable
- [x] 實作 `_format_value_range(values: list[float], unit: str) -> str`

## Task 2: 實作 group_evidence 主函式

- [x] 實作 `group_evidence(evidence, *, time_window_days=7, similarity_threshold=0.70)`
- [x] Step 1: 按 `(normalized_source, kind)` 分桶
- [x] Step 2: 桶內按 `_extract_metric_key` 結果再分子群
- [x] Step 3: 子群內用 Jaccard 相似度（複用 `trust.scoring._jaccard` / `_normalize`）做 fallback 聚合
- [x] Step 4: 過濾例外（direction 不同、flagged 條目）
- [x] Step 5: 排除跨時間窗口的配對
- [x] Step 6: 每群組選 trust 最高者為 representative
- [x] Step 7: 計算 trend + value_range + latest_value
- [x] 確保 `union(g.member_indices) == set(range(len(evidence)))`（全覆蓋不漏項）

## Task 3: 單元測試 evidence_grouper

- [x] 建立 `tests/test_evidence_grouper.py`
- [x] 測試：同源同 kind 同指標不同值 → 正確聚合
- [x] 測試：趨勢計算 rising/falling/stable/None
- [x] 測試：不同 direction → 不聚合
- [x] 測試：不同 kind → 不聚合
- [x] 測試：flagged (manipulation > 0) → 獨立成組
- [x] 測試：空 list → 空回傳
- [x] 測試：全部不相似 → 每筆獨立一組
- [x] 測試：數值提取邊界（中文單位、無單位、多數值句）
- [x] 測試：時間窗口外 → 不聚合
- [x] 確認所有測試通過

## Task 4: 整合 evidence_grouper 到 build_report

- [x] `schema.py::Report` 新增 `evidence_groups: list[dict] | None = field(default=None)`
- [x] `data_contracts.py` 對應 schema 更新（optional array）
- [x] `orchestrator.py::build_report` 在組裝 evidence list 完成後呼叫 `group_evidence()`
- [x] 將 `evidence_groups` 結果寫入 `Report.evidence_groups`
- [x] 修改 `facts` 產出邏輯：群組 ≥ 2 筆用聚合摘要格式
- [x] 修改 `key_basis` 產出邏輯：同群組只取一條 BasisItem，evidence_idx 帶全組
- [x] 加入面向多樣性守則：連續 key_basis 不允許相同 (source, kind) 組合

## Task 5: 後端 API 擴充

- [x] `web.py` 的 `/api/analyze` response 加入 `evidence_groups` 欄位
- [x] 向後相容：`evidence_groups` 為 null/missing 時既有消費端不受影響
- [x] 確認 snapshot 序列化正確（含 evidence_groups）

## Task 6: 前端 TypeScript 型別與 utility

- [x] `frontend/src/lib/types.ts` 新增 `EvidenceGroup` interface
- [x] `Report` interface 新增 `evidence_groups?: EvidenceGroup[]`
- [x] 建立 `frontend/src/lib/evidenceGrouping.ts`
  - `buildGroupMap(groups)`: 群組索引反查
  - `isGrouped(idx, groupMap)`: 判定某 evidence 是否在多筆群組中
  - `getGroupForIdx(idx, groupMap)`: 取得所屬群組

## Task 7: 前端 EvidenceTable 群組渲染

- [x] 新增 `EvidenceGroupRow` 元件
  - 折疊態：代表摘要 + trend badge + value_range + 成員數 pill
  - 展開態：所有成員各自渲染為 `EvidenceRow`
- [x] 修改 `EvidenceTable` 主渲染邏輯
  - 有 `evidence_groups` → 按群組渲染
  - 無 `evidence_groups` → 原始 flat 顯示（向後相容）
- [x] Trend badge 樣式：rising=green arrow up, falling=red arrow down, stable=gray dash
- [x] 成員數 pill 樣式：`"4 筆觀測"` 灰色小標籤
- [x] Accessibility: 群組用 `<details>/<summary>` 或等效 ARIA 模式

## Task 8: 前端測試

- [x] `EvidenceTable` 測試：帶 evidence_groups → 群組渲染正確
- [x] `EvidenceTable` 測試：不帶 evidence_groups → flat 渲染（回歸）
- [x] `EvidenceGroupRow` 測試：折疊/展開切換
- [x] `evidenceGrouping.ts` 測試：buildGroupMap 邊界案例

## Task 9: 整合驗證與回歸

- [x] 執行完整 pytest suite，確認無回歸
- [x] 執行前端 vitest，確認無回歸
- [x] 手動驗證：使用既有 sample_data 或 fixture 跑 pipeline，確認 evidence.json 筆數不變
- [x] 手動驗證：report 不出現 3+ 條幾乎相同的事實
- [x] 手動驗證：前端群組展開可追溯全部原始 claim_id
- [x] 確認 lint / type-check 通過
