# TrustForge 比賽交付導向 UI/UX 優化計劃

- 日期：2026-07-29
- Issue：#934
- 目標分支：`develop`
- 狀態：待 CEO 核准後拆分實作 Issue
- 範圍：整合目前 UI/UX 實測、HOYA BIT 官方命題、DIGITIMES 決賽規則與既有交付流程；本文件不包含產品程式碼修改

## 1. 決策摘要

TrustForge 的 UI/UX 優化不應以「增加艦橋視覺效果」或「把產品限制成三種題型」為目標，
而應讓評審與一般使用者能快速完成以下任務：

1. 以任意自然語言提出加密市場問題。
2. 由 Hermes AI Agent 自動理解意圖、規劃工具與選擇分析策略。
3. 在一次執行中得到可讀的 Final Report、可抽查的 Evidence List 與可理解的
   Execution Log。
4. 從每個關鍵結論追溯到真實證據、資料時間、正反訊號、限制與可能推翻條件。
5. 在正式執行失敗、部分資料缺失或服務降級時，仍保留輸入、工作識別與可說明的結果。

### 1.1 重要更正：官方三種題型不是 Hermes 白名單

HOYA BIT 官方文件列出的「多源整合、假設驗證、比較分析」是範例題型與競賽驗證案例，
不是要求產品只接受三種固定輸入。產品設計必須遵守：

- 主入口接受任意自然語言市場問題，不要求使用者先把問題分類。
- Hermes AI Agent 自動判斷問題意圖、需要的資產數量、分析策略、工具與資料來源。
- 問題可能同時包含多源整合、比較、假設檢驗、事件影響、風險、來源查證、歷史變化或
  其他組合，不應因不符合三個固定 label 而拒絕。
- 官方三種題型只作為交件測試矩陣，證明系統至少能完整處理主辦方已知範例。
- BTC、ETH、SOL、BNB、XRP 是競賽當日指定幣種池；一般產品能力不因此被永久限縮。
- 舊的 `QuestionType`、`mode` 或 route 可保留為執行策略與向後相容契約，不應直接成為
  使用者必須理解的產品限制。

本決策取代
`docs/plans/PLAN-competition-question-format-ui-2026-07-26.md`
中「主要 UI 改成官方三題型 selector」與「比賽模式只有三項題型」的產品假設。該文件
仍可作為 API mapping 與官方範例測試的歷史參考，但不得再作為限制 Hermes 問題類型的依據。

## 2. 權威來源與優先序

若不同文件衝突，採以下優先序：

1. 2026-08-01 現場公告、上傳平台規則與主辦方最新書面通知。
2. DIGITIMES 2026 官方活動頁及決賽須知。
3. `docs/competition/COMPETITION-OFFICIAL.md` 保存的 HOYA BIT 官方命題附件。
4. Repository 內部摘要、計劃與 checklist。

目前已知差異：

- `SUBMISSION-CHECKLIST.md` 寫投稿截止 2026-08-01；官方公開頁描述為
  8/1–8/2 的 30 小時競賽期間內上傳。確切截止時間與平台以 8/1 現場公告為準。
- Repository 內部分工作坊、報名日期已落後官方公開頁；不影響產品設計，但不可用於
  最終現場排程。
- `finale-submission.zip < 50MB` 目前視為內部安全門檻；若現場平台公布不同上限，
  以平台規則為準。
- 官方決賽須知明確要求僅使用 AWS 服務提供的基礎模型；TrustForge 應以 Bedrock
  路徑作正式競賽執行，不依賴較寬鬆的舊附件解讀。

## 3. 比賽交付與評分對 UI/UX 的要求

### 3.1 四件核心交付物

| 交付物 | UI/UX 必須提供 |
|---|---|
| Final Report | 結論、關鍵依據、證據如何支撐、信心、限制、資料不足、可能推翻條件 |
| Evidence List | `source`、`fetched_at`、`content_reference`、`related_claim`，以及 URL／query／時間範圍等回溯資訊 |
| Execution Log | 時戳、工具呼叫、資料取得、分析流程、失敗與降級、總執行時間 |
| Source / Config | Agent code、設定、版本、執行說明與 AWS 架構入口 |

此外，決賽提案需涵蓋命題連結、企業數據應用、技術架構、生成式 AI 技術應用與
Live Demo。UI 必須能在評審簡報中直接支撐這五段敘事，不要求評審到 repository
內尋找證據。

### 3.2 評分權重轉換

| 評分項目 | 權重 | 介面對應 |
|---|---:|---|
| 主題切合度 | 30% | 多源整合、證據回溯、矛盾、信心校準、限制必須在報告主層可見 |
| 技術可行性 | 25% | Agent 規劃、工具、資料流、AWS 執行與 log 可被理解 |
| 商業應用性 | 20% | 快速降低資訊雜訊，讓使用者形成可採信而非盲從的判斷 |
| 創意度 | 15% | Trust scoring、反方證據、來源獨立性與跨源分歧具體呈現 |
| 完成度 | 10% | 任意題目到四件交付物的流程完整、穩定、流暢 |
| Kiro 加分 | +10% | 保留可驗證的 Kiro 開發與整合證據，但不把內部證據暴露為公開 secret |

因此，比賽前 UI 投資順序應是 Report → Evidence → Execution Log → Demo 穩定性，
而不是先擴張裝飾動畫或低關聯的周邊模組。

## 4. 現況 UI/UX 實測

本輪以目前 branch 的實際前端，在 1440×900 與 390×844 檢查首頁及分析工作區。

### 4.1 已具備的優點

- 已有完整 HERMES 品牌語言與清楚的差異化視覺。
- 已有多幣 overview、信任拆解、跨源分歧、Evidence、Execution Log 與多種狀態元件。
- 已有繁中／英文、theme、reduced motion、focus ring、contrast 與 mobile geometry
  測試基礎。
- 分析工作支援 durable job、輪詢、reload reconnect、忙碌重試與錯誤恢復的底層能力。
- 已有多個針對遮擋、截斷、觸控目標與錯誤狀態的 regression test。

### 4.2 主要問題

#### 資訊架構

- 首頁與子頁使用兩套不同導覽與工作空間心智模型。
- 核心功能、次要工具、設定與展示模組混在同一層 header。
- 有些功能只能從弱曝光文字入口或直接 URL 抵達，難以理解產品範圍。
- 四件競賽交付物分散在報告、歷史、狀態或下載操作中，沒有單一交付中心。

#### 首頁

- 桌面首屏同時競爭注意力的區域過多：導覽、教學、提問、Galaxy、Trust Score、
  Breakdown、分歧與 pipeline。
- 新手教學覆蓋 Galaxy 主區域，但核心提問入口仍位於狹窄左欄。
- 主要 CTA 的視覺權重低於中央動畫。
- `ONLINE`、`LIVE UPLINK` 與 `Runtime unavailable` 可能同時出現，造成狀態矛盾。
- 「即時」「snapshot」「示範」的資料性質不容易從視覺上判斷。

#### 手機

- 390px 下主導覽成為超長水平帶，部分項目位於視窗外。
- 首頁首屏看得到 Galaxy，卻看不到主要問題輸入與分析 CTA。
- 幣種按鈕約 38×25px，低於舒適的 44×44px 觸控目標。
- 分析頁 header 約 171px；主內容只剩約 296px 寬，艦橋側軌與底部 engine deck
  仍持續占用空間。
- 固定外框加內部 viewport scrolling 容易形成巢狀捲動陷阱。

#### 閱讀與文案

- 大量 8–12px 等寬字適合 telemetry，但不適合作為報告正文或投影簡報內容。
- 技術狀態詞、中英混排與艦橋術語提高理解成本。
- Trust Score 與四個分項先呈現數字，白話含義、限制與下一步較弱。
- Error、empty、stale、partial 與 unavailable 已有個別處理，但缺少跨頁一致語言。

#### 維護性

- 目前累積不少 Nxx 修補註解與局部 responsive 例外，顯示缺少統一 App Shell、
  typography、狀態矩陣與 breakpoint 策略。
- 大量 inline style 與裝飾特例會提高後續視覺回歸成本。

## 5. 目標使用流程

### 5.1 一般／評審共用主流程

```text
任意自然語言問題
  → Hermes 理解意圖與涉及資產
  → 顯示「系統理解」供使用者確認
  → Hermes 規劃分析策略與工具
  → 單次 durable execution
  → Final Report
      ├─ 關鍵結論 ↔ Evidence
      ├─ 信心／限制／反方／推翻條件
      ├─ Execution Timeline
      └─ 交付中心：Report / Evidence / Log / Source-Config
```

### 5.2 官方範例驗證

UI 不提供封閉的三題型選單，但 acceptance test 必須以官方範例驗證：

1. 多源整合：單幣、指定期間、多資料類型、一致性判斷。
2. 假設驗證：單一假設、正反證據、最終判斷與理由。
3. 比較分析：兩資產、市場位置、流動性、關注度與風險敞口。
4. 混合／非範例問題：例如事件衝擊加跨幣比較，證明 Hermes 不受三類限制。

### 5.3 正式執行保護

因官方原則上只有一次正式執行且最多 15 分鐘，UI 需區分：

- 問題草稿／範例填入：不建立正式工作。
- 系統理解預覽：顯示資產、意圖、預計資料與策略，不消耗正式執行。
- 正式執行：確認後建立唯一 durable job。
- reconnect：reload 或切頁後接回同一 job，不重複提交。
- retry：只有安全且符合後端 idempotency contract 時才提供。
- partial result：部分來源失敗時保留可用交付，誠實標記影響。

## 6. 分階段優化計劃

### Phase 0 — 交件契約與 UX 基準

優先級：P0；工期：0.5–1 天；依賴：無。

交付：

- 建立 Report／Evidence／Log／Source-Config UI contract。
- 定義任意問題的 intent preview contract，不把三種範例做成白名單。
- 固定 1440×900、1024×768、768×1024、390×844、320×568 基準畫面。
- 建立狀態矩陣：initial、planning、queued、running、partial、stale、completed、
  failed、reconnecting、unauthorized。
- 記錄五條核心任務的操作數與可見性基準。

驗收：

- 每個頁面都有主要任務、主要 CTA、空白／錯誤／部分資料狀態定義。
- 所有後續 Issue 都能對應官方交付物或評分項目。
- UI contract 明確允許 unknown／combined intent，禁止 client-side 題型白名單拒絕。

### Phase 1 — 統一 App Shell 與響應式導覽

優先級：P0；工期：1–2 天；依賴：Phase 0。

交付：

- 首頁與功能頁共用一致導覽心智模型。
- 核心入口：首頁、分析、比較／研究工作區、歷史。
- 監控入口：來源狀態、成本。
- 次要入口：資產脈絡、Peer Metrics、Help、Settings 收進「更多」或工具中心。
- 手機使用簡潔 top bar 加 drawer／核心底部導覽，不使用超長水平帶。
- 手機隱藏不承載任務的 side rail、hologram bay chrome 與常駐 engine deck。

驗收：

- 390px 下所有核心功能不需水平捲動即可抵達。
- 控制項主要觸控目標至少 44×44px。
- 任一核心頁兩次操作內可回首頁或前往另一核心功能。
- 鍵盤焦點順序與視覺順序一致。
- 320px–1440px 不存在 page-level horizontal overflow 或巢狀捲動陷阱。

### Phase 2 — 任意問題優先的 Hermes Composer

優先級：P0；工期：1–2 天；依賴：Phase 0、Phase 1。

交付：

- 首屏主角改為自然語言問題輸入，而非 Galaxy。
- 提供資產 optional hint；使用者可在問題中直接指定一個或多個資產。
- 提供常見問題範例，點擊只填入、不自動送出。
- 提交前顯示 Hermes 理解：
  - 涉及資產
  - 問題意圖
  - 預計分析策略
  - 主要資料類型
  - 是否需要比較／假設驗證
- 系統無法確定時，以可編輯 clarification 補足，不強迫選三類。
- 正式執行使用清楚、唯一的 CTA；Galaxy 降為結果探索或次要概覽。

驗收：

- 首次使用者在 10 秒內能找到問題輸入與主要 CTA。
- 390px 首屏能看到 composer 或明確「開始提問」入口。
- 任意非三範例問題能進入 Hermes planning，不被前端拒絕。
- 範例按鈕永不自動建立正式 job。
- 正式送出前可看見系統理解，且能修正錯誤資產。
- 重複點擊、reload 與 reconnect 不會產生重複正式工作。

### Phase 3 — Final Report 資訊層級

優先級：P0；工期：1–2 天；依賴：Phase 0、Phase 2。

交付：

- 報告首屏依序呈現：
  1. 市場判斷／問題回答
  2. 信心及其白話含義
  3. 三項關鍵依據
  4. 主要矛盾或反方證據
  5. 已知限制與缺少資料
  6. 可能推翻結論的條件
  7. 資料涵蓋與完成時間
- 第二層才放完整推理、圖表、證據表與技術 telemetry。
- 事實、推論、結論與系統建議採一致 semantic badge。
- 任意問題的回答結構可彈性擴充，不用三份互斥 report template。

驗收：

- 結果完成後，1440×900 首屏可回答「判斷什麼、為什麼、可信度與限制」。
- 評審 30 秒內能理解核心價值，不需先開 drawer。
- 低信心／資料不足時不使用強烈買賣暗示。
- 每項關鍵依據都有穩定 claim ID，可前往 Evidence。
- 圖表具有文字摘要，不單靠顏色或動畫傳遞結果。

### Phase 4 — Evidence List 抽查體驗

優先級：P0；工期：1–2 天；依賴：Phase 0、Phase 3。

交付：

- Evidence 依 claim 分組，支援 report ↔ evidence 雙向跳轉。
- 顯示來源類型、名稱、URL、取得時間、引用片段／數值／query、資料期間、
  支持／反對／中立、來源可信度與獨立性。
- 對缺欄位、失敗來源、過期資料與重複來源群組明確標示。
- 提供 JSON／CSV 下載，下載內容與畫面使用同一資料來源。
- 手機採可讀卡片或欄位優先顯示，不把完整寬表硬塞進 viewport。

驗收：

- 任一關鍵結論兩次操作內可看到原始證據。
- Evidence 四個必備欄位在 UI 與下載檔一致。
- 外部連結安全、可辨識且保留 fetched time／content reference。
- 空資料與部分資料不製造來源、引用或關係。
- 官方三範例與一個混合問題皆能通過 claim-evidence trace audit。

### Phase 5 — Execution Log 與 Agent 可行性展示

優先級：P0；工期：1 天；依賴：Phase 0、Phase 2。

交付：

- 評審版 timeline：Planning → Sources → Verify → Reason → Report → Package。
- 原始 JSONL 下載保留工程稽核用途。
- 顯示工具、資料源、開始／完成／失敗、重試、降級、耗時與資料量。
- 顯示 run ID、release、commit、Bedrock model／region 與總時間預算。
- 清楚證明核心判斷由 TrustForge pipeline 產生，而非直接轉交第三方分析結論。
- 內部參數、secret、private endpoint、敏感 metadata 不進公開 UI／下載。

驗收：

- 15 分鐘 budget 與目前狀態清楚可見。
- 任一步驟失敗可指出受影響的 report claim 或資料類型。
- 正式 run 有唯一且不可混淆的識別。
- log 可用於重跑判斷，但 UI 不承諾主辦方不允許的任意正式重跑。
- 此 phase 涉及公開 telemetry 與成本資訊，實作前需 harper 安全審查。

### Phase 6 — 交付中心與 Presentation Mode

優先級：P0；工期：1 天；依賴：Phase 3–5。

交付：

- 每個完成 run 提供單一「交付成果」區：
  - 查看／匯出 Final Report
  - 查看／下載 Evidence List
  - 查看／下載 Execution Log
  - 查看 Source／Config／執行說明
  - 下載完整競賽包
- Presentation Mode：
  - 放大主要結論與關鍵證據
  - 隱藏設定、版本噪音與不相關實驗入口
  - 保留資料真實性、時間與 runtime 狀態
  - 支援主辦提供電腦的 1440×900 投影
- 公開 HTTPS Demo 不暴露 secret，資源載入失敗時有可說明 fallback。

驗收：

- 四件交付物集中在同一 run context，不需跨多個工作區拼裝。
- 匯出檔案共享 run ID、版本與生成時間。
- 完整競賽包可離線檢查。
- Presentation Mode 正文至少 16px，核心結論建議至少 20px。
- `ONLINE`、`LIVE`、`unavailable` 等狀態不互相矛盾。

### Phase 7 — 視覺系統、文案與可及性

優先級：P1；工期：1–2 天；依賴：Phase 1、Phase 3。

交付：

- 正文不低於 14px；技術 metadata 原則上不低於 12px。
- 等寬字限於數字、代碼、log 與短狀態。
- 建立一致 Button、Input、Card、Alert、Badge、Tabs、Skeleton、EmptyState。
- 將 hard-coded visual values 收斂到 semantic tokens。
- 限制同一畫面的光暈、掃描線、邊框與裝飾層級。
- 將 `PROXY`、`UPLINK`、`TELEMETRY LOCKED` 等術語配上使用者語言。
- 繁中與英文完整切換；error copy 採「發生什麼、影響什麼、下一步」。
- 支援 200% zoom、鍵盤、reduced motion、觸控 glossary 與 focus return。

驗收：

- WCAG AA 對比。
- 200% zoom 不遺失操作或內容。
- 所有互動可用鍵盤完成。
- loading 使用 `aria-live`；純裝飾 hologram 不進閱讀順序。
- zh-TW／en 均通過長字串、溢出與投影 eye scan。

## 7. 建議 Issue 拆分與依賴

| 順序 | 建議 Issue | 優先 | 依賴 | 安全／成本審查 |
|---|---|---:|---|---|
| UX-C01 | 交件 UI contract、狀態矩陣與基準截圖 | P0 | 無 | 否 |
| UX-C02 | 統一 App Shell 與 mobile navigation | P0 | C01 | 否 |
| UX-C03 | 任意自然語言 Hermes Composer + intent preview | P0 | C01、C02 | 成本路徑需 harper |
| UX-C04 | 正式 run confirmation、idempotency 與 reconnect UX | P0 | C03、#883 系列 contract | harper |
| UX-C05 | Final Report 第一層資訊架構 | P0 | C01、C03 | 否 |
| UX-C06 | Claim ↔ Evidence 雙向追溯與下載 | P0 | C05 | 外部連結安全檢查 |
| UX-C07 | Execution Timeline 與公開 log redaction | P0 | C01、C04 | harper 必要 |
| UX-C08 | 交付中心與 Presentation Mode | P0 | C05–C07 | 公開輸出安全檢查 |
| UX-C09 | Typography、semantic components 與文案降噪 | P1 | C02、C05 | 否 |
| UX-C10 | 響應式、a11y、官方範例與混合問題 release gate | P0 | C02–C09 | 否 |

不要用一個大 PR 同時實作所有階段。每張 Issue 應保持可獨立驗收、可回滾，且原則上
不超過 12 小時；超過即再拆分。

## 8. 測試與 eye-scan 矩陣

### 8.1 問題能力

- 官方多源整合範例。
- 官方假設驗證範例。
- 官方雙資產比較範例。
- 混合問題：事件影響 + 跨幣比較 + 來源查證。
- 無法明確分類但仍合理的市場問題。
- 缺少資產、含多資產、中文、英文及中英混合問題。

預期：全部進 Hermes planning；必要時要求補充資訊，不因 client-side 類型清單被拒絕。

### 8.2 狀態

- 初始空白。
- planning。
- queued／running。
- reload reconnect。
- partial sources。
- stale data。
- network error／server busy。
- analysis failure。
- completed。
- export failure。
- unauthorized admin／restricted output。

### 8.3 視窗與輸入

- 1440×900 投影桌面。
- 1024×768 主辦設備保守尺寸。
- 768×1024 平板。
- 390×844 手機。
- 320×568 最窄支援。
- 200% zoom。
- `prefers-reduced-motion`。
- zh-TW／en。
- keyboard-only。

### 8.4 Local gates

每個實作 PR 至少執行受影響測試，並在 push 前執行 repository-local gate：

```bash
cd frontend
npx vitest run
npx tsc -b
npx oxlint
npm run build
npm run test:mobile-geometry
npm run test:contrast

cd ..
.githooks/pre-push
```

所有命令以 repository `AGENTS.md` 規範執行。GitHub Actions 未啟用，不作為替代 gate。

UI PR 必須在實際 branch 進行 desktop／mobile eye scan，檢查：

- 資料真實性與時間標示。
- overflow、遮擋與 nested scrolling。
- loading → partial／error／success 狀態轉換。
- CTA 可達性與重複提交。
- Report ↔ Evidence trace。
- 中英文長字串。
- 投影可讀性。

## 9. 比賽版 Definition of Done

- 任意自然語言加密市場問題都能進入 Hermes planning；三種官方題型不是輸入限制。
- 官方三個範例題型與至少一個混合問題完成端到端驗證。
- 競賽指定幣種池均完成 smoke test，但一般產品不因此永久限縮資產支援。
- 正式流程在 15 分鐘預算內完成，或以誠實且可交付的 partial result 降級。
- 一次 durable run 產出 Final Report、Evidence List、Execution Log 與 Source／Config
  導覽。
- Report 包含結論、依據、信心、限制、資料不足與可能推翻條件。
- 每個關鍵 claim 可回溯到真實 Evidence；Evidence 必備欄位完整。
- 可證明核心推理與判斷由 TrustForge Agent pipeline 產生。
- 使用 AWS Bedrock 的模型、region、run 與 release 證據可驗證。
- reload 可接回既有工作，不重複正式提交。
- 公開 HTTPS Live Demo 不暴露 secret、private endpoint 或敏感 metadata。
- 1440×900 投影與 390×844 手機均無遮擋、水平溢出或不可達 CTA。
- 繁中／英文、keyboard、reduced motion、200% zoom 通過。
- 四件交付物可集中查看、分別下載並封裝離線備份。
- Kiro 使用證據保留且不污染公開產品介面。
- `.githooks/pre-push` 綠燈、`/codex-review` 無 unresolved finding、UI eye scan 與
  commit-bound reviewer attestation 完整。

## 10. 執行治理

1. gray（CPO）以本計劃建立 scoped implementation issues。
2. CEO 核准範圍與優先序後才開始實作。
3. 每張 Issue 建立獨立 branch、測試與 PR，不直接開發於 `main`／`develop`。
4. 成本敏感的正式執行 UX 與公開 telemetry／log 需 harper 審查。
5. 每個 PR 執行本機 pre-push gate、對抗 `/codex-review` 與實際 branch eye scan。
6. 不以管理員 override、自我 approval 或跳過 reviewer attestation 的方式合併。
7. 合併後在 merged branch 重跑 local gate。
8. 生產部署只走 repository 明定的 release workflow，完成 health 與正式問題流程親驗。

## 11. 非本計劃範圍

- 不修改 Trust scoring、模型訓練、來源評分或核心推理演算法。
- 不替缺少的 Evidence、EcoLink 或 telemetry 製造示範資料。
- 不因官方範例而刪除 Hermes 的一般問題、資產或分析策略能力。
- 不把 Admin、成本或內部 metadata 無差別公開給評審。
- 不在本文件 PR 中順手實作任何 Phase 1–7 產品變更。
