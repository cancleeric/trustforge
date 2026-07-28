# AgentCore 封存分支移植對照（Issue #793）

原始碼永久保留於 `archive/agentcore-integration-20260728`。本次以現行
`develop` 為基底逐項移植，不整批合併舊分支。

| 封存內容 | 處理 | 現行落點／理由 |
|---|---|---|
| pipeline wrapper、支援幣種 tool | 已接線 | `app/TrustForge/main.py` → `agentcore_runtime` → 現行受控 pipeline |
| AgentCore Memory session | 移植 | `trustforge.agent.agentcore_memory`，預設關閉且不硬依賴 SDK |
| Online evaluation 設定 | 保留現行設定 | `agentcore/agentcore.json` 已有 QualityMonitor |
| Flask BFF 與重複 HTML frontend | 不直接移植 | 現行 React frontend 與 API 已取代；保留於封存分支供追溯 |
| AgentCore 狀態 badge | 重新整合 | 走同源狀態 API，不採舊版直連 8080／CORS 探測 |
| `auto_analyze.py` | 已重新整合 | `agentcore_event.py` + one-shot script；不安裝、不自動啟動 daemon |
| `auto_upgrade.py` | 由現行治理流程取代 | 舊版直接讀 SQLite 且以 LLM 自審，不符合現行資料與審查規範 |
| `cost_tracker.py` | 由真實 ledger 取代 | 舊版用字數估 token 與固定單價，不能作正式成本真相來源 |
| `.cache`、trace、deployed-state、logs | 排除 | runtime 產物，不可進 release artifact |

## 不遺失原碼保證

1. 封存 commit `50829a2` 與其 15 個獨有 commit 已有具名遠端分支。
2. 本文件記錄每一組功能的移植或取代位置。
3. 未被直接移植的程式不是刪除，而是因現行架構已有更安全實作而留在封存分支。
4. production 部署必須等本分支完整 gate、審查與合併後另行決定。
