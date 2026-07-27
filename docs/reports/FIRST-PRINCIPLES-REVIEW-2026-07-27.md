# 第一性原理審視報告

> 日期：2026-07-27<br>
> 類型：開發用策略／架構審視報告<br>
> 範圍：TrustForge 專案定位、Evidence-first 閉環、護城河、風險、下一步建議。<br>
> 對應 public docs 草稿來源：`TrustForge-devlog/docs/18-first-principles-review.html`

## 1. 執行摘要

TrustForge 應被視為 **Evidence-first 加密市場分析系統**，不是一般聊天式投資助理。它的核心能力是把多源、雜訊高、容易被操縱的市場資訊拆成可評分、可追溯、可反駁的 Evidence，再由 Bedrock 產生受控分析報告。

| 問題 | 第一性判斷 |
|---|---|
| 核心方向是否成立？ | **成立。** 加密市場真正缺的是可信判斷流程，不是更多流暢文字。 |
| 最大護城河是什麼？ | **Trust Layer + Evidence Binding + 可回放紀錄。** 不是模型本身，也不是 UI。 |
| 目前最大弱點？ | **功能很多，但主線容易被稀釋。** 需要把所有功能收斂到 Evidence-first 閉環。 |
| 下一步最該做什麼？ | **把可信分析閉環打硬。** 先補 evidence-bound report、abstain、live/fixture 邊界、run replay，再推更多亮點。 |

一句話結論：

> TrustForge 的第一性價值不是「AI 會分析幣」，而是「AI 的每個市場判斷都能被追溯、質疑、反駁與回放」。後續所有功能都應服務這條 Evidence-first 閉環。

## 2. 第一性問題

加密市場裡，使用者的根本痛點不是「沒有 AI 回答」，而是：

| 根本問題 | 表面症狀 | TrustForge 應回應的能力 |
|---|---|---|
| 來源品質差異巨大 | 官方公告、新聞、KOL、社群喊單與機器人轉發混在一起。 | Source reputation、source type、content hash、fetched_at。 |
| 市場敘事容易被操縱 | 假新聞、拉盤喊單、回音室、多帳號轉貼。 | Manipulation penalty、cross-source corroboration、contrarian evidence。 |
| LLM 容易產生合理但不可查證的結論 | 文字流暢，但無法驗證根據。 | Evidence-bound report、claim_id、execution log。 |
| 使用者不知道該信什麼 | 看完仍只能憑感覺。 | Decision state、confidence、limits、could flip、abstain。 |
| 競賽需要展示技術深度 | 單純 RAG 摘要容易被視為套殼。 | Trust Kernel、可稽核資料流、AWS Bedrock 受控生成。 |

因此 TrustForge 的根本任務是：

```text
把「混亂市場資訊」轉成「可查證、可反駁、可回放的市場判斷」。
```

## 3. 可信分析價值鏈

TrustForge 的正確價值鏈應固定為：

```text
Raw Sources
  ↓
Documents
  ↓
Claims
  ↓
Trust Scoring
  ↓
Evidence Binding
  ↓
Contrarian / Limits
  ↓
Final Report
  ↓
Execution Log / Replay
```

反模式：不得先讓 LLM 寫結論，再找 citation 裝飾。這會把 TrustForge 退化成「看似有來源的聊天機器人」。

## 4. 真正護城河

模型不是護城河。Bedrock、Claude、GPT、Gemini 都會進步，單純 prompt 或 RAG 摘要也容易被複製。TrustForge 真正難抄的是：

| 護城河 | 理由 |
|---|---|
| Trust Layer | 資料進 LLM 前先做來源信譽、交叉佐證、時效與操縱風險評分。 |
| Evidence Binding | 每個重要判斷都可回到 source、claim、content reference 與 fetched_at。 |
| Abstain / Decision State | 把「不知道」產品化，避免低證據時硬講。 |
| Contrarian Evidence | 不是只找支持證據，而是固定要求反方與可推翻條件。 |
| Run Replay | 保留 snapshot、policy revision、model id、execution log 與 evidence hash，讓判斷可稽核。 |
| Approval-gated outer framework | 外框可改善流程，但不能自行改核心權重、正式結論或 production。 |

## 5. 目前狀態判斷

| 面向 | 判斷 | 說明 |
|---|---|---|
| 核心概念 | 成立 | README 與架構主軸明確是「多源資訊的信任提煉」。 |
| 工程厚度 | 已非玩具 | 目前粗估有 138 個 Python modules、227 個 backend test files、57 個 frontend test files。 |
| Trust Kernel | 有骨架 | 已有 Evidence、Report、TrustScore、scoring、orchestrator、execution log。 |
| 新手脈絡模組 | 原型可驗證 | Asset Context、Glossary、Peer / EcoLink 已有 API / UI / tests，但仍有 fixture 與覆蓋限制。 |
| 外框治理 | 架構已補 | 已整理 31 個控制面模組、5 個 policy family、sandbox、人審 gate 與 rollback。 |
| Continuous analysis | 尚未形成完整運作循環 | 目前應避免宣稱 fully autonomous self-improvement。 |
| Release-ready 宣稱 | 需保守 | 若要宣稱 release-ready，需完整 pre-push gate、live smoke 與 commit-bound evidence。 |

## 6. 主要風險

| 風險 | 說明 | 處置建議 |
|---|---|---|
| 功能拼盤化 | Dashboard、外框、自動升級、Glossary、EcoLink 等亮點很多，容易稀釋主線。 | 所有 roadmap item 必須回答：是否強化 Evidence-first 閉環？不能就降級。 |
| 過度宣稱 | fixture / sample / live 邊界若沒標清，容易被認為全即時真資料。 | UI、API、文件、簡報都固定標示 data mode、source、freshness。 |
| LLM 結論先行 | 若報告由 LLM 主導結論，Trust Layer 會變成裝飾。 | 把 Evidence → Claim → Score → Judgment 設成不可變管線。 |
| 外框越權 | 自我改善若能碰 Trust weights / model gate / production code，可信度會崩。 | 維持 `approval_required=true`、`automatic_apply=false`，且 sandbox attestation 必須綁定 artifact hash。 |
| 測試只跑 targeted | Targeted tests 可證明局部，但不能證明 release-ready。 | 重要 PR 必須跑 repo-local pre-push gate，並附結果。 |

## 7. 不可變原則

1. **Evidence 先於結論。** 先建立 Evidence / Claim / Score，再生成市場判斷。
2. **缺資料要 abstain。** 資料不足、來源衝突、時間窗過舊時，寧可不給方向性結論。
3. **歷史記憶不能成為 Evidence。** 歷史問答可幫助理解使用者意圖，但不得進入當前市場證據或 deterministic Trust scoring。
4. **外框只能改善流程，不能改真相。** 外框不得自行修改 Trust weights、Evidence binding、正式報告、模型 gate、成本上限或部署。
5. **live / fixture / sample 必須無法誤解。** 每個報告與視覺元件都應明確揭露資料模式與 freshness。

## 8. 建議路線

### P0：把可信分析閉環打硬

- 每份 report 完整綁定 Evidence：conclusion、key basis、limits、could flip、contrarian evidence 都要能回到 claim/source。
- 把 abstain 做成正式產品狀態：資料不足時不要 fallback 成漂亮廢話。
- 全面標示 live / fixture / sample / stale；正式資料未接前不可暗示即時觀測。
- 每輪 run 固定記錄 source snapshot、policy revision、model id、execution log 與 evidence hash。
- 以完整 repo-local pre-push gate 作為 release-ready 前置條件。

### P1：把新手脈絡整合進主分析流程

- 報告上方直接呈現 Asset Context：例如 ARB 顯示 Layer 2、Ethereum settlement、gas token ETH、governance token。
- Glossary 不只標 market_judgment，而是 section-aware 覆蓋 facts、inferences、key basis、limits。
- 同層比較與 EcoLink 應在 compare / analysis result 主路徑出現，並維持「可能相關，不是因果」措辭。
- 前後端 glossary catalog 改成生成或同步驗證，避免雙份漂移。

### P2：讓外框從「可治理」變成「真的跑過一圈」

- 先啟動 durable analysis jobs / stage runs / failures / diagnostics。
- 從低風險 family 開始 proposal：優先 `report`、`source`、`evaluation`。
- 完成第一個 sandbox → human approval → activation → rollback drill 的受控循環。
- 暫不碰 `analysis`、`model-gate`、`core-adjacent`，避免越過可信邊界。

## 9. 對外定位與禁語

| 建議說法 | 避免說法 |
|---|---|
| Evidence-first 加密市場分析 Agent。 | 最強 AI 投資顧問。 |
| 先把多源資訊拆成可評分、可追溯、可反駁的 Evidence，再由 Bedrock 產生受控分析報告。 | AI 自動預測幣價。 |
| 支援 approval-gated outer-framework improvement。 | AI 已可全自動自我升級。 |
| 目前部分能力為 repo 支援或 fixture prototype，正式 demo 需標示資料模式。 | 全幣種、全即時、全自動市場監控已完成。 |

## 10. 第一性評分表

| 項目 | 評分 | 理由 |
|---|---:|---|
| 問題定義 | 9 / 10 | 切中「可信市場判斷」而非單純聊天。 |
| 技術差異化 | 8 / 10 | Trust Layer、Evidence Binding、Execution Log 有護城河潛力。 |
| 產品聚焦 | 6 / 10 | 功能多，主線需要更強收斂。 |
| 可信邊界 | 7 / 10 | 文件與治理已補，但 live / fixture / release-ready 語言仍需嚴格控管。 |
| 可運作閉環 | 6 / 10 | 核心與外框架構存在，但 continuous improvement 尚未完整跑通。 |
| 競賽展示價值 | 8 / 10 | 如果 demo 聚焦 Evidence-first，而不是功能大雜燴，辨識度高。 |

## 11. 最終建議

停止追逐更多無關亮點，先把「可信分析閉環」做成不可被質疑的主線。

TrustForge 贏面不在於 AI 多會說，而在於它能證明自己為什麼這樣說、何時不該說、以及哪些證據會推翻它。
