# TrustForge 退化風險分析與緊急優化改善計劃

> Release 更新：本文件作為 v0.16.0 的風險清單輸入。資料契約、不可變
> `source_events`、quality/quarantine、lineage、point-in-time feature store、
> 五年 replay 與 desktop 顯示退化已完成；其餘項目繼續由 Hermes canonical
> backlog 追蹤，不以本文件重複宣告完成。

> 日期：2026-07-17  
> 範圍：只針對 TrustForge 專案是否滿足 HOYA BIT 黑客松主辦方要求進行分析與改善規劃。  
> 原則：本文件為分析與計劃文件，不包含源碼修改。

## 1. 執行摘要

TrustForge 的核心架構已切中主辦方命題：「加密市場分析 AI Agent：多源資訊的信任提煉」。目前已具備多源資料整合、信任評分、證據回溯、Execution Log、官方 OHLCV lineage、AWS Bedrock-only 模型入口與 Live Demo 架構。

但距離正式投稿仍有數個緊急風險，主要集中在「正式資料源」、「正式模型執行」、「現場 Demo 穩定性」與「可交付證據」。若不處理，現場可能退化成 sample/offline demo，導致評審認為作品只有架構展示，沒有完成正式競賽要求。

最急迫的改善方向如下：

1. 接上 HOYA BIT 正式 endpoint / auth / schema，至少完成 ticker canary。
2. 跑通真 Bedrock 模型 smoke，正式報告不得出現 offline placeholder。
3. 建立最小 online QA matrix，驗證五幣與三題型可跑。
4. 封存 Live Demo desktop/mobile evidence 與完整錄影。
5. 投稿前完成 secret scan、內網 reference 清理、官方資料公開風險判斷。

## 2. 主辦方需求對照

| 主辦方要求 | TrustForge 現況 | 判定 |
|---|---|---|
| 多源資料整合 | 已有 OHLCV、news、social、onchain、regulatory、CoinGecko、HOYA connector 架構 | 部分滿足 |
| 有層次推理 | Report schema 與 Markdown 有事實、推論、結論、關鍵依據 | 滿足 |
| 不確定性與限制說明 | Report 會輸出 limits、could_flip、contrarian | 滿足 |
| 可回溯 Evidence | Evidence schema 有 `source/fetched_at/content_reference/related_claim`，OHLCV 有 SHA-256 lineage | 滿足 |
| 具洞察分析角度 | 有 trust layer、反方證據、操縱風險、cross-source signal、insights | 滿足 |
| 15 分鐘限制 | ExecutionLog 內建 900 秒預算與來源耗時紀錄 | 滿足 |
| 4 件交付物 | CLI 產出 `report.md`、`evidence.json`、`execution_log.jsonl`，repo 為 Source/Config | 滿足 |
| 使用 HOYA BIT 企業資料 | 官方 OHLCV 已使用；HOYA online endpoint 尚未取得 | 部分滿足 |
| Live Demo URL | Web/API/前端架構存在，但 production viewport evidence 未完整封存 | 部分滿足 |
| AWS 基礎模型限制 | Bedrock 是唯一模型入口；正式 smoke 尚待模型與憑證 | 部分滿足 |
| AWS Kiro 加分 | 尚未確認是否採用 | 未滿足 |

## 3. 目前退化風險

### 3.1 HOYA BIT 正式資料退化為 stub

**現況**

`hoyabit-ticker` 只有在 `TRUSTFORGE_HOYABIT_TICKER_URL` 設定後才會啟用正式 connector。未設定時仍會回傳 stub placeholder。`depth`、`orderbook`、`trades` 目前是 `NotImplemented`。

**風險**

主辦方明確要求使用企業提供資料。若正式報告中 HOYA BIT 資料只停留在官方 OHLCV 或 sample/stub，會降低「企業數據應用」說服力。

**改善目標**

- 取得 HOYA BIT endpoint、auth、schema。
- 至少完成 ticker online canary。
- 若時間允許，優先接 depth/orderbook，因為這更貼近「智慧交易」與市場流動性分析。

### 3.2 正式報告退化為 offline placeholder

**現況**

離線分析可成功產出交付物，但 report 的推論段可能出現 `[OFFLINE] would answer` 文字。這對本機 demo 可接受，對正式投稿不可接受。

**風險**

正式交付若出現 offline placeholder，會直接削弱生成式 AI 應用與 Bedrock 合規性。

**改善目標**

- 正式提交前必須跑非 `--offline` 模式。
- 設定 `AWS_REGION` 與 `BEDROCK_MODEL_ID`。
- 保存真 Bedrock run 的 report/evidence/log。
- Execution Log 中需可看到 Bedrock model、token 或成本紀錄。

### 3.3 真線上來源穩定性不足

**現況**

backlog 指出 `240 題 online QA` 尚未在正式憑證與配額下完整執行。CoinGecko、Reddit 等來源仍有 production reliability gate 未達連續成功條件。

**風險**

現場抽題時，如果多個來源 429、timeout 或 cache miss，系統可能退化成少源或離線結果。這會影響主題切合度、多源整合度與完成度。

**改善目標**

- 緊急版不追完整 240 題，先跑最小 matrix：5 幣 × 3 題型。
- 每次保存成功率、p95 耗時、失敗來源、Evidence 筆數與 report decision state。
- 對不穩來源建立明確 fallback 說明，不把缺資料偽裝成低信任。

### 3.4 Demo 現場觀感風險

**現況**

本機離線 CLI 在網路受限時會嘗試讀 AWS admin config，產生長 traceback，但最後仍成功。Live Demo frontend/backend 已存在，但 production desktop/mobile viewport evidence 尚未完整封存。

**風險**

現場看到 traceback 或 network error 會被視為系統不穩。沒有截圖或錄影時，Live Demo 完成度也不易被證明。

**改善目標**

- 正式 demo 環境避免印出 AWS admin config traceback。
- 封存 desktop 與 mobile 截圖。
- 錄製完整流程：選題、分析、Evidence、Execution Log 下載。
- Demo 頁面要清楚顯示資料模式、模型模式、降級原因。

### 3.5 公開投稿安全與資料授權風險

**現況**

`docs/competition/SUBMISSION-CHECKLIST.md` 已列出 public release SOP，但尚未完成 final gate。官方 OHLCV 是否可公開、內網 references 是否外露、secret scan 是否乾淨都需確認。

**風險**

若臨時要求 GitHub public，可能重散布主辦資料或外露內部 Gitea / infra 資訊。

**改善目標**

- 先確認主辦方接受 private repo + collaborator，還是必須 public。
- 若必須 public，移除官方資料並清 git history。
- 跑 secret scan 與內網字串掃描。
- README 改成競賽公開版，不含內部基建資訊。

## 4. 緊急改善計劃

### P0：比賽前必須完成

| 項目 | 目標 | 驗收標準 | 依賴 |
|---|---|---|---|
| HOYA BIT ticker canary | 正式企業資料進 Evidence | `hoyabit-ticker` 產出 live Evidence，非 stub | HOYA endpoint/auth/schema |
| 真 Bedrock smoke | 正式報告不再 offline | report 無 `[OFFLINE]`，log 有 Bedrock 呼叫紀錄 | AWS region/model/權限 |
| 15 題 online QA matrix | 驗證五幣三題型可跑 | 5 幣 × 3 題型均有 report/evidence/log 或明確失敗原因 | live sources/cache |
| Demo evidence | 完成可展示證據 | desktop/mobile 截圖、完整錄影、Live Demo URL | 部署環境 |
| 投稿安全 gate | 避免外洩與資料授權問題 | secret scan、內網 reference scan、public/private 決策記錄 | owner 決策 |

### P1：高優先改善

| 項目 | 目標 | 驗收標準 |
|---|---|---|
| 降級訊息整理 | 現場不出 traceback | CLI/Web 只顯示人類可讀降級原因 |
| README 狀態對齊 | 避免評審誤解完成度 | README 改成 official/live/sample 三態描述 |
| source reliability summary | 評審能看懂缺源原因 | 報告或 dashboard 顯示每來源 ok/empty/failed |
| AWS Kiro 決策 | 爭取 +10% 或留下不採用理由 | 文件紀錄是否使用 Kiro |
| submission package index | 投稿材料可快速核對 | 一頁列出 report/evidence/log/demo/repo/architecture |

### P2：可延後但有加分價值

| 項目 | 目標 |
|---|---|
| depth/orderbook connector | 強化智慧交易與流動性分析 |
| historical replay | 增強信任評分可驗證性 |
| calibration/outcome labeling | 改善 confidence 校準，但不宣稱價格預測機率 |
| production 7-day reliability evidence | 提升正式上線可信度 |

## 5. 建議執行順序

1. **鎖定正式資料契約**
   - 向 HOYA BIT / 主辦方確認 endpoint、auth、schema、使用限制。
   - 若沒有新 endpoint，明確記錄只能使用官方 OHLCV 與公開資料。

2. **跑真 Bedrock smoke**
   - 設 `AWS_REGION`、`BEDROCK_MODEL_ID`。
   - 使用 BTC `multi_source` 跑一次非 offline。
   - 保存 `report.md`、`evidence.json`、`execution_log.jsonl`。

3. **跑最小 online QA matrix**
   - 幣種：BTC、ETH、SOL、BNB、XRP。
   - 題型：multi_source、hypothesis、comparison。
   - 輸出表格：成功/失敗、耗時、Evidence 數、來源失敗清單、是否降級。

4. **封存 Demo 證據**
   - 開 Live Demo。
   - desktop/mobile 截圖。
   - 錄影完整流程。
   - 確認可以下載 Evidence 與 Execution Log。

5. **整理正式 submission package**
   - Final Report。
   - Evidence List。
   - Execution Log。
   - Source/Config。
   - AWS 架構圖。
   - Live Demo URL。
   - GitHub repo / collaborator 權限。

6. **投稿前安全 gate**
   - secret scan。
   - 內網 reference scan。
   - 官方資料公開風險確認。
   - public/private 策略決策。

## 6. 完成定義

正式交付前，至少需滿足以下條件：

- report 不含 `[OFFLINE]` placeholder。
- Evidence 每筆都有 `source`、`fetched_at`、`content_reference`、`related_claim`。
- Execution Log 具備來源蒐集、claim extraction、trust reasoning、evidence assembly、report delivery 五階段紀錄。
- 至少一筆 HOYA BIT 正式企業資料或明確的官方 OHLCV lineage 進入 Evidence。
- Live Demo URL 可用，並已錄製一次完整分析流程。
- 若發生來源失敗，報告以 `limits` 說明，不偽裝為低信任或正常資料。
- 投稿 repo 不含 secret、內網資訊或未授權公開資料。

## 7. 當前判定

TrustForge 不是從零開始補功能，而是進入「正式化與去退化」階段。核心算法與架構已足以支撐主題，但必須把正式資料、正式模型、正式 Demo、正式 evidence 封存補齊，才算真正滿足主辦方要求。

目前整體狀態：

- 主題切合度：高
- 技術可行性：中高
- 完成度：中
- 商業應用性：中高
- 現場風險：中高，主要來自資料與部署證據

建議短期目標不是新增大型功能，而是消除退化路徑、封存可稽核證據、確保現場執行不掉回 sample/offline。
