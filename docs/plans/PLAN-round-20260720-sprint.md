# TrustForge 下一輪開發計劃

> 日期：2026-07-20（週一）
> 距決賽：**12 天**（8/1–2）
> 當前版本：v0.16.16 | closed 21 issues today | 30 open issues remaining
> 撰寫：gray (CPO)

---

## 優先序分析（P0 → P3）

### P0 — 競賽存活（不做 = 失格或重大扣分）

| # | Issue | Size | 狀態 | 說明 |
|---|-------|------|------|------|
| 280 | backend deploy health interruption | S-M | ready-now | Production bug，下次部署前必修 |
| 283 | CEO Loop starvation watchdog | M | ready-now, PR#284 open (1898行) | 排程靜默停工，已有大 PR |
| 202 | 正式 Bedrock smoke artifact | S | blocked-external (AWS cred) | 證明非 offline 可跑 |
| 203 | Online QA mini matrix (5幣×3題型) | M | blocked-external | 依賴 #202 完成 |
| 204 | Live Demo evidence 錄影封存 | S | needs-evidence | 依賴 #203 |
| 205 | 投稿前安全 gate (secret scan) | S | blocked-external | 最後一週做 |
| 199 | HOYA BIT 深度合約空函式 + 誠實化 | M | ready-now | 評審看穿 = 扣分 |
| 167 | HOYA BIT 真實資料接線 | M-L | blocked-external (待 spec) | 依賴官方 endpoint |
| 220 | Finale 總控 issue | — | tracking | 不產程式碼，追蹤用 |

### P1 — 核心技術深度（影響 30% 主題切合 + 15% 創意）

| # | Issue | Size | 狀態 | 說明 |
|---|-------|------|------|------|
| 195 | 動態信譽缺外部校準 | L | blocked-external | 需歷史多源，目前可做簡化版 Platt |
| 196 | 信心校準硬編查表 → 統計校準 | M-L | blocked-external | 同上，但可先做 ECE 度量 |
| 197 | Conformal Prediction 未 wire 進 production | M | blocked-external | 依賴 #198 |
| 198 | 連接器無異質歷史序列 | L | blocked-external | 根因，阻塞 195/196/197 |
| 241 | RAG 暫緩決策 | — | decision-doc | 維持不動 |

### P2 — 商業應用 / UX（影響 20% 商業應用 + 10% 完成度）

| # | Issue | Size | 狀態 |
|---|-------|------|------|
| 245 | 離線模式 timeout bug | S-M | ready-now |
| 232 | 首次試用 UX 重做 | L | ready-now |
| 231 | 低動態模式 | S-M | ready-now |
| 169 | AWS Kiro +10% bonus | — | 待拍板 |

### P3 — 工程品質 / 長線（不影響決賽得分）

| # | Issue | Size | 狀態 |
|---|-------|------|------|
| 281 | AgentCore 切換 UI | L | blocker for upgrade-modules |
| 250 | 來源連接器接 AgentCore upgrade_status | S | blocker for #254/#255/#258 |
| 252 | Embedding model-gate | L | blocker for #260/#261/#263/#266 |
| 254/255/258 | Data plane 觀測 | S | blocked by #250 |
| 260/261/263/266/267/271 | Intelligence/Delivery plane modules | M-L | blocked by #252 |
| 237 | 資料來源優化 Plan | — | long-term plan |
| 104/113 | SecOps/NetOps 告警落地 | S | needs-evidence |
| 170 | Mars Li 確認 AWS model 限制 | — | 已過時效窗口 |
| 8 | Reddit OAuth | S | blocked-external |

---

## 本輪目標（5 個具體目標）

**GOAL 1：Production Deploy 零中斷**
— 修復 #280，確保下次部署不再 16s 斷線

**GOAL 2：CEO Loop 恢復自動產出**
— 處理 PR#284 / #283，讓排程真正能派工、有 watchdog

**GOAL 3：HOYA BIT 誠實化 + Bedrock Smoke Ready**
— 完成 #199 誠實化（不假裝已接），準備 #202 正式 Bedrock 驗證

**GOAL 4：前端可用性修復**
— 修 #245 timeout bug，確保離線 demo 不卡死

**GOAL 5：決賽投稿管線就位**
— #205 安全 gate + #203 QA matrix + #204 錄影，週 2 起依序執行

---

## 執行批次（每批 2-3 issues，按 dependency 排序）

### Batch 1（7/20–7/22，本週前三天）— 解除部署阻塞

| Issue | 負責 | 說明 |
|-------|------|------|
| **#280** | 開發 | 分析 nginx/systemd cutover gap，實作 blue-green 或 health-gate 策略 |
| **#283 / PR#284** | 開發 | **決策：精簡 merge**（見下方風險段落）。PR 檔案合理、測試充分(470行測試)，做 focused review 後 merge |
| **#245** | 開發 | 前端 timeout 修復（離線模式 10s→合理值 or graceful degradation） |

**完成門檻**：部署一次生產無中斷、CEO loop 下一輪有派工記錄、離線 demo 不 timeout。

### Batch 2（7/22–7/25）— 核心誠實化 + Bedrock

| Issue | 負責 | 說明 |
|-------|------|------|
| **#199** | 開發 | HOYA BIT self-check 告警 + references 誠實化 + 測試兩路徑 |
| **#202** | 開發+驗證 | 設 AWS cred 跑一次非 offline smoke，封存 artifact |
| **#170** | 隊長 | 補記 Mars Li 確認結果（若已口頭確認，補文字紀錄 close） |

**完成門檻**：`hoyabit-ticker` 未設時明確告警不假裝、Bedrock smoke artifact 存在。

### Batch 3（7/25–7/28）— QA Matrix + UX

| Issue | 負責 | 說明 |
|-------|------|------|
| **#203** | 開發 | 5 幣 × 3 題型 online mini matrix，保存退化報告 |
| **#231** | 前端 | 低動態模式（prefers-reduced-motion + toggle），Size S |
| **#169** | 隊長拍板 | AWS Kiro +10%：**建議做**，邊際成本低（本 repo 已全程在 Kiro 開發），只需整理證據 |

**完成門檻**：15 組 QA 結果有 artifact、動態模式可切換。

### Batch 4（7/28–7/31）— 封裝投稿

| Issue | 負責 | 說明 |
|-------|------|------|
| **#205** | 安全 | Secret scan + 內網 reference 清理 + repo 決策(public/private) |
| **#204** | 全員 | Desktop/mobile 截圖 + 完整流程錄影，不出現 traceback |
| **#104/#113** | 維運 | SecOps 告警落地（若時間允許） |

**完成門檻**：投稿 checklist 全勾、Demo 錄影封存、zero traceback。

### 8/1–2 決賽當天

- 依現場公告調整幣種/題型
- 跑正式 pipeline → 產出 4 交付件
- 簡報（技術深度 + AWS 架構圖 + Live Demo URL）

---

## 風險與取捨決策

### 決策 1：PR#284（1898 行）— Merge 而非重寫

**理由**：
- 17 個檔案中 470 行是測試，259 行是 shell script 重構，結構清晰
- 新增模組各自獨立（watchdog/state/runtime_guard/lane_cleanliness）
- 12 天內重寫不合理，且功能正確解決 #283 的所有 checklist
- **行動**：做 2 小時 focused code review + `/codex-review`，修小問題後 merge

### 決策 2：#281 AgentCore 切換 UI — 本輪不做

**理由**：
- Size:L（7hr），且被它阻塞的 30 個 upgrade-module issues 全是 P3
- AgentCore 整合對決賽評分零影響（評審看 Trust Layer 核心，不看 AgentCore）
- 決賽後再做

### 決策 3：P1 核心演算法（#195/#196/#197/#198）— 做文件不做程式碼

**理由**：
- 全部 blocked-external（需歷史多源資料，目前不存在）
- 但評審會看**技術深度論述** → 在簡報/報告中清楚說明設計意圖與已實作部分
- **行動**：不寫新程式碼，但在決賽簡報補充「Dawid-Skene + Conformal 設計」段落

### 決策 4：#169 Kiro +10% — 建議做

**理由**：
- 本 repo 已全程在 Kiro 中開發（Steering files / Hooks 均存在）
- 只需整理一份 Kiro usage evidence document（session 截圖 + feature usage）
- 投入 ≤ 2hr 可換 10% 加分，ROI 極高
- **行動**：隊長 7/25 前拍板，7/28 前整理完證據

### 決策 5：#232 首次試用 UX 重做 — 本輪降級

**理由**：
- Size:L，需要重新設計流程
- 現有 UI 功能完整，只是學習曲線陡
- 決賽評審是專業人士，不是首次用戶
- **行動**：不做完整重做，但可在 #231 低動態模式時順手優化 landing page 文案

### 風險登記

| 風險 | 影響 | 緩解 |
|------|------|------|
| HOYA BIT 官方 endpoint 一直沒給 | 決賽無法展示真實 HOYA BIT 資料 | #199 誠實化：明確標示 stub，用官方 OHLCV 作基準 |
| Bedrock quota/access 異常 | #202/#203 無法完成 | 保留 offline fallback，展示 pipeline 架構能力 |
| PR#284 有隱藏 bug | CEO loop 壞更嚴重 | Review 重點放 state transition + error path |
| 時間壓縮 Batch 3/4 | 錄影不完整 | Batch 3 提前 1 天、Batch 4 壓縮到 2 天 |

---

## 不做清單（明確列出本輪不做的 + 理由）

| Issue/工作 | 理由 |
|------------|------|
| **#281** AgentCore 切換 UI (size:L) | 對決賽評分零影響；blocker 的下游全是 P3 |
| **#250/#252** 及其下游 (#254/#255/#258/#260/#261/#263/#266/#267/#271) | AgentCore/upgrade plane 長線工作，決賽不考 |
| **#237** 資料來源優化 Plan | Phase 0-6 規劃完整但是長線工作，12 天無法執行 |
| **#232** 首次試用 UX 重做 (size:L) | 評審是專業人士，現有 UI 功能完整 |
| **#195/#196/#197/#198** 演算法深化程式碼 | blocked-external（無歷史多源資料），改為簡報論述 |
| **#8** Reddit OAuth | blocked-external（需真人辦憑證），Fear&Greed 已頂替 |
| **#241** RAG 暫緩 | 已決議暫緩，維持不動 |

---

## 每日 Standup 追蹤指標

- Open P0 issues（目標：7/28 前歸零）
- Bedrock smoke 是否完成（Y/N）
- QA matrix 通過率（目標 ≥ 13/15）
- 部署中斷秒數（目標 = 0）
- CEO loop 連續成功派工輪次

---

## 附錄：評分對照

| 評分項 (權重) | 本輪交付對應 |
|--------------|-------------|
| 主題切合度 (30%) | #199 HOYA BIT 誠實化、#203 QA matrix 證明多源整合可用 |
| 技術可行性 (25%) | #280 部署修復、#202 Bedrock smoke、CEO loop 穩定 |
| 商業應用性 (20%) | #245 timeout 修復、#231 動態模式、#204 錄影展示 |
| 創意度 (15%) | 簡報論述 Trust Layer 設計（Dawid-Skene / Conformal / provenance）|
| 完成度 (10%) | 5幣3題型 end-to-end、4 交付件完整 |
| Kiro +10% | #169 拍板 + 整理使用證據 |

---

*下次 review：7/23 (三) sprint mid-check*
