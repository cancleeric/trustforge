# 退件修正：事實聚合 Production 缺陷修復

> Issue: #862（退件重開）
> 前置 spec: done--fact-aggregation-862
> Labels: data-quality, fix, production

## 背景

PR #880 交付了事實聚合引擎（`evidence_grouper.py`）並整合進 `orchestrator.py`，但審查退件指出四項 production 缺陷與一項流程缺陷。本 spec 專門處理 production code 修正。

## 退件必修項目

### FR-1: 聚合隔離 claim direction

**問題**：`group_evidence()` 分桶只看 `(normalized_source, kind)`，沒有檢查 Evidence 所對應的 claim direction，可能把 bullish 與 bearish 內容合成同一組。

**修正**：分桶 key 加入 direction 維度。Evidence 本身不直接帶 direction 欄位，需從 `related_claim` 標籤或信任組件推斷：
- 若 Evidence 來自 `brief.supporting`（`related_claim == judgment_tag`）視為正方
- 若 Evidence 來自 `brief.contrarian`（`related_claim == "反方／低信任訊號"`）視為反方
- 正方與反方不得聚合在同一組

### FR-2: 同 metric 數值單位驗證

**問題**：同一 metric_key 下的 Evidence 可能帶不同單位（如 "67500 USD" vs "67.5 千美元"），直接算 value_range 會產生跨單位的錯誤值域。

**修正**：
- `_finalize_group()` 在計算 value_range 前，驗證 member 提取到的 unit 是否一致
- 不一致時：`value_range = None`（不顯示數值範圍），`trend = None`（不判趨勢）
- 不拆組（聚合本身仍成立，只是不顯示數值摘要）

### FR-3: 來源正規化沿用 canonical alias

**問題**：`evidence_grouper._normalize_source` 只做 `strip().casefold()`，沒有沿用 `trustforge_core.source_identity.canonical_source()` 的 alias 規則（如 `coindesk.com` → `coindesk`）。

**修正**：
- `_normalize_source()` 改為呼叫 `canonical_source()`（來自 `trustforge_core.source_identity`）
- 確保聚合層與 orchestrator、scoring 的來源正規化口徑一致

### FR-4: key_basis 前三條保證不同面向

**問題**：面向多樣性守則只在 `len(deduped_basis) >= 3` 時才跳過重複 `(source, kind)` 組合，不保證最前面 3 條分別來自不同面向。

**修正**：
- 前 3 條 BasisItem 必須各自有不同的 `(normalized_source, kind)` 組合
- 第 4 條起允許重複面向（但仍受群組去重約束）
- 若可用的不同面向不足 3 種，從現有面向中各取一條填充

## 非功能需求

- 所有修正須附邊界測試
- 測試涵蓋：反向 claim 不聚合、不同單位不算值域、來源 alias 正確收斂、前三條不同面向
- 不變式：`union(g.member_indices) == set(range(len(evidence)))` 仍成立
- evidence.json 輸出筆數不受影響（只是呈現聚合行為改變）

## 驗收條件

- [ ] bullish 與 bearish Evidence 不在同一 EvidenceGroup
- [ ] 同 metric 不同單位時 value_range 回 None
- [ ] `_normalize_source()` 使用 `canonical_source()`，與 orchestrator 同口徑
- [ ] key_basis 前 3 條的 (source, kind) 組合互不相同
- [ ] 全部既有測試通過（無回歸）
- [ ] 新 branch → PR → reviewer attestation 完整流程
