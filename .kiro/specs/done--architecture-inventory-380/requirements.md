# Spec：架構盤點總報告 (#380)

> Issue: #380
> Priority: P1-core
> Type: documentation
> Downstream: #381, #382, #383, #384, #385, #386

---

## Requirements

### R1: 完整模組列表

產出 `docs/architecture/ARCHITECTURE-INVENTORY-2026-07-21.md`，內含：

- **Trust Kernel**：信任評分核心（scoring / dawid_skene / conformal / insights / stance_cache / kernel facade）
- **Outer Layer**：Agent 編排、Bedrock 封裝、Pipeline、Ingestion 連接器、Policy 執行器
- **Operations**：budget_guard / cost_model / upgrade_control / module_telemetry / scheduler / admin_config / backend_registry
- **Frontend/Web**：web.py（SPA）、Live Demo
- **Infra**：App Runner / Lambda / Docker / DynamoDB / SSM

### R2: 狀態標記

每個模組標記其當前狀態：
- `✅ implemented` — 已完成且有測試覆蓋
- `🔧 partial` — 已有程式碼但功能不完整
- `📋 stub` — 佔位/宣告但未真正接線
- `🗓️ planned` — 已規劃但無程式碼

### R3: 依賴關係圖

以 ASCII 或 Mermaid 繪製模組間依賴（至少三層：Kernel → Outer → Ops/Web）

### R4: 技術債清單

列出主要缺口與改進空間：
- 從 issue #380 的「主要缺口」段落整理
- 對應後續子 issue（#381~#386）

### R5: 與 issue #380 結論對齊

報告內容須反映 issue 中的「已確認完成」與「主要缺口」段落。

---

## Acceptance Criteria

- [x] `docs/architecture/ARCHITECTURE-INVENTORY-2026-07-21.md` 存在且完整
- [x] 涵蓋所有 src/trustforge/ 下的主要模組
- [x] 每模組有狀態標記
- [x] 含依賴關係圖
- [x] 含技術債清單
- [x] 與 issue #380 結論一致
