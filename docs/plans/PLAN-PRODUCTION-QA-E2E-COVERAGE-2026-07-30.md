# TrustForge 正式 QA、E2E 與覆蓋率補齊計劃

> 工單：#1093  
> 依據：#1090 正式壓測、比賽方五幣要求、現有 release gate  
> 狀態：計劃完成，實作待依相依性拆單執行  
> 原則：部署成功不等於競賽驗收成功；任何報告驗收必須使用當次回傳的真實 ID。

## 1. 目標與非目標

本計劃建立兩條彼此獨立、都必須通過的正式門檻：

1. **Production Deployment Gate**：證明版本、artifact、服務、worker、IAM、
   runtime contract 與回滾能力正確。
2. **Competition Acceptance Gate**：證明正式公開 HTTP、真實資料來源、五幣、
   三題型、五方向、報告內容、下載、手動插隊與 10 分鐘 SLA 符合比賽需求。

本計劃不以增加測試數量為目標，也不把 mock／offline 綠燈當成 production
證據。Production 因來源不足而 `abstain` 可以是正確結果，但來源缺漏不得被
誤標為「competition-ready」。

## 2. 現況基準與已知缺口

### 2.1 目前已有

- mandatory pre-push：
  - backend pytest parallel + serial；
  - global line coverage hard floor 75%；
  - data contract、YAML、stub scan；
  - 24 題 offline competition QA；
  - frontend Vitest、lint、TypeScript/Vite build。
- 2026-07-30 實跑基準：
  - backend line coverage **85%**；
  - `src/trustforge/web.py` line coverage **77%**；
  - backend **6,973 parallel + 14 serial** 通過；
  - frontend **606** tests 通過。
- local public HTTP E2E：
  - `tests/test_multi_angle_public_e2e.py`；
  - `tests/test_comparison_e2e.py`；
  - AGOS HTTP E2E；
  - `scripts/run_multi_angle_release_gate.py` 的 no-AWS guard。
- deployment canary：
  - `activate_release.sh` 驗 health、worker stability；
  - `verify_production_analysis_report.py` 送一題 BTC/risk 並等待 report。
- frontend 已安裝 Playwright，但目前主要用於幾何／eye scripts，尚無正式
  production critical-journey Playwright suite。

### 2.2 不能接受的缺口

| 缺口 | 風險 |
|---|---|
| 部署 canary 只跑 BTC/risk 一題 | 報告能產生，不代表五幣、三題型、五方向可用 |
| production 黑箱測試沒有單一總控 runner | 證據散落 `/tmp`，無一致 schema、JUnit、趨勢與 release binding |
| 五幣來源完整性未作 hard gate | social 全失敗、部分 official/macro/news 缺漏仍可能被誤報成功 |
| 手動插隊只在 unit test 驗 priority 欄位 | 無法證明 scheduled backlog 下真的先取件；#1090 曾等待約 164 秒 |
| `/api/analysis-job` 完成只驗 `result` 是 dict | 未驗 Evidence、Execution Log、引用、下載檔內容 |
| multi-angle local gate 無真 AWS | 抓不到 IAM 子 action、DynamoDB drift、PITR、daily budget 問題 |
| Playwright 未覆蓋正式關鍵旅程 | 自動選題、送出、loading、結果、比較、mobile 狀態可能在瀏覽器壞掉 |
| frontend 無 line/branch/function coverage gate | 606 tests 不能量化哪些 UI 邏輯完全沒走到 |
| global backend coverage 會掩蓋關鍵模組退步 | 總體 85% 仍可能讓 release／web／analysis-flow 新分支零覆蓋 |
| 沒有 mutation／contract gap 指標 | line covered 不代表錯誤條件、abstain、backpressure 真的被驗 |

## 3. 測試金字塔與執行頻率

| 層級 | 執行時機 | 環境 | Hard gate |
|---|---|---|---|
| L0 Unit/contract | 每次 pre-push | 本機、無 AWS | 是 |
| L1 Local HTTP integration | 每次 pre-push | 真 HTTP handler + 臨時 durable stores | 是 |
| L2 Browser E2E | 每次 release candidate | Playwright + 本機 candidate backend | 是 |
| L3 Deployment Gate | 每次 production activation | EC2 localhost/candidate port | 是 |
| L4 Competition Acceptance | **每次 production 部署後** | 公開正式 URL + 真 AWS/資料源 | 是 |
| L5 Soak/load | 每日與重大 release | production，受成本上限控制 | release 前至少一次全量通過 |

L4 不得因成本理由省略成單幣；可以限制同時併發與每日成本，但五幣全矩陣沒跑完
就不得把 release 標成 accepted。若 L4 失敗，部署本身可以維持健康狀態，但
release receipt 必須標為 `deployed_not_accepted`，禁止寫 `production_accepted`。

## 4. Production Deployment Gate

此 gate 不回答比賽功能是否完整，只回答新版本是否正確落地。

| ID | 驗收項目 | Hard assertion |
|---|---|---|
| DG-01 | public health/version | HTTPS 200；frontend/backend 版號與 release manifest 相同 |
| DG-02 | artifact lineage | active digest、git SHA、version、S3 pointer、EC2 manifest 一致 |
| DG-03 | service stability | web、analysis daemon、scheduler active；觀察窗內無 restart loop |
| DG-04 | runtime contract | Web/daemon 的 model、atomic table/config、shared DB path 完全一致 |
| DG-05 | IAM drift | 模擬並真實 canary 驗 `TransactWriteItems` 及 `ConditionCheckItem` 等子 action |
| DG-06 | DynamoDB authority | table key schema、SSE、PITR、tags、今日 budget item/config 正確 |
| DG-07 | rollback | previous pointer 與服務設定備份存在，rollback dry verification 通過 |
| DG-08 | minimal canary | 一題 manual analysis 真完成；此項只證明 queue 基本存活 |

任一 DG 失敗：activation fail closed，不進 Competition Acceptance。

## 5. Competition Acceptance Matrix

### 5.1 五幣 × 三題型

固定幣種：BTC、ETH、SOL、BNB、XRP。

| 題型 | 數量 | 必驗內容 |
|---|---:|---|
| multi_source | 5 | 同一份報告整合多源資料，不是五份來源摘要 |
| hypothesis | 5 | 假設、支持、反證、限制、結論強度 |
| comparison | 5 | **同一份比較報告**同時包含兩幣、可比時間窗與 coin-swap symmetry |

共 15 份主報告。每份必須驗：

- HTTP 200、正式 schema、coin/question/type/snapshot identity；
- Report、Evidence、Execution Log 三層皆存在；
- evidence reference `[source:#index]` 可反查且 ID 穩定；
- facts/inference/conclusion 分離；
- confidence、limits、abstain 不互相矛盾；
- 來源不足時必須 abstain，不得 overclaim；
- comparison 不得退化成兩份獨立分析並排。

### 5.2 五幣 × 五方向

每幣送一次 atomic multi-angle，共 5 batches、25 jobs、5 synthesis reports：

- risk、sentiment、fundamentals、news、catalyst 各一；
- 五 job 使用同一 snapshot；
- authority allocation/slot/outcome/settlement cardinality 正確；
- `reserved_total` 最終為 0；
- synthesis 的 angle job IDs 必須與當次 POST 回傳完全一致；
- 五方向從 POST 到完整 synthesis **≤600 秒**。

### 5.3 資料來源完整性

每幣每份報告記錄 source family，而不是只看 evidence count：

- price/market；
- on-chain；
- news；
- social；
- official/regulatory；
- macro（題目需要時）。

來源分成：

- **required**：題目語義明確需要，缺少即該 case fail；
- **applicable**：該幣／題目適用，缺少則 degraded；
- **not_applicable**：必須附可稽核理由，不得用來掩蓋 connector 失敗。

Competition release 的全域 hard gate：

- 五幣不得有 connector authority/crash；
- required family coverage 100%；
- URL、取得時間、source identity、freshness 可驗；
- social/official/macro 等整個 family 全滅時，release 必須 fail。

## 6. Manual Priority、併發與壓力

### 6.1 真插隊 E2E

1. 先建立足以佔用 worker 的 scheduled backlog。
2. backlog 正在執行時送 manual question 與一個 manual multi-angle batch。
3. 從 durable stage timestamps 驗：
   - manual queue priority 小於 scheduled；
   - 下一個可用 worker slot 必須先取 manual；
   - manual 第一階段開始等待目標 ≤30 秒；
   - 五方向 synthesis ≤600 秒。
4. 不允許只斷言 DB priority 欄位而沒有真實執行順序證據。

### 6.2 負載模型

| 場景 | 目的 | 預期 |
|---|---|---|
| 5 個不同幣 multi-angle 同時送 | 比賽全矩陣 | 25 jobs 完成或可說明 abstain，無 authority crash |
| 10 路 analyze/comparison 混合 | 一般 burst | 無 5xx；延遲與成本受控 |
| 同 key 雙擊/重送/多 tab | idempotency | 同 batch/job IDs，不重複扣款 |
| 不同 key 超 budget | cost backpressure | 明確 409，零 partial jobs |
| rate limit 超限 | abuse backpressure | 明確 429，零 side effect |
| Bedrock timeout/daemon restart | recovery | bounded retry、無 double charge、可 settlement |

輸出 p50/p95/max、queue wait、stage duration、HTTP status distribution、成本與
authority cardinality。壓測不能只報 requests/sec。

## 7. 報告與下載產物驗收

當次 API 回傳的 `job_id`/`snapshot_id` 是唯一追蹤來源：

1. POST 取得真 ID。
2. bounded poll 到 terminal；讀 stage-level error。
3. 取得 report、evidence、execution log。
4. 走 UI／API 提供的下載入口，真下載檔案。
5. 驗 HTTP 200、Content-Type、非零大小、格式 magic bytes 或 JSON parse。
6. 下載內容的 job/snapshot/question/evidence IDs 必須與當次 run 相同。

若產品目前只有 JSON API、沒有使用者可見下載入口，該 case 標為功能缺口，
不能把 `curl -o /tmp/file.json` 宣稱為「下載功能已驗收」。

每次 L4 產出：

```text
out/acceptance/<release-id>/
  summary.json
  junit.xml
  cases/*.json
  reports/*
  http/*.headers
  browser/desktop/*.png
  browser/mobile/*.png
  browser/traces/*.zip
  source-coverage.json
  latency.json
  cost.json
  manifest-binding.json
```

報告須遮蔽 token/secret，不得保存 authorization header。

## 8. Playwright Browser E2E

新增 `frontend/e2e/` 與 production-safe Playwright config，至少覆蓋：

| ID | Desktop + mobile journey |
|---|---|
| UI-01 | 首頁載入、動態自適應星系可見、無 overflow/console error |
| UI-02 | 自動選題按鈕抽一題，題目確實寫入輸入框 |
| UI-03 | 送出 manual analysis，loading/progress/partial/completed 狀態完整 |
| UI-04 | 五方向結果、abstain、error、retry 狀態可辨識 |
| UI-05 | comparison 是同一份比較報告，不是兩張獨立 report |
| UI-06 | Evidence reference 點擊／展開可對應來源 |
| UI-07 | Execution Log 可讀且不洩漏 secret |
| UI-08 | 報告下載真成功並驗內容 |
| UI-09 | 429/503/timeout 顯示明確錯誤且可恢復 |
| UI-10 | refresh/back/重送不重複建立或扣款 |

viewport 至少：

- Desktop 1440×900；
- Mobile 390×844；
- breakpoint 邊界 900px 與 901px，永久防止星系 top-offset 回歸。

Browser suite 使用穩定 `data-testid`；不得依中文文案全文或任意 sleep。
等待條件以 API response、DOM state 或 bounded poll 為準。

## 9. 覆蓋率策略

### 9.1 Backend

保留 global coverage，但提高為「不低於基準」：

- global line：先鎖 **85%**，不得下降；
- `web.py`：先鎖 **77%**，補 E2E 後目標 **≥82%**；
- `analysis_flow.py`、`multi_angle_batch_store.py`、`budget_guard.py`、
  `deployment_control.py`：各自建立 module floor；
- branch coverage 開啟後先記錄 baseline，再分兩次 release 拉高，禁止直接用
  exclude 規則美化數字。

同時使用 pytest coverage contexts 分辨：

- unit；
- local-http-e2e；
- release-gate。

這可以看出「某行只有 unit mock 走過」而沒有真 HTTP journey 的情況。

### 9.2 Frontend

加入 Vitest V8 coverage：

- statements/lines/functions/branches 各自輸出；
- 第一版門檻依真實 baseline 設定，不准猜數字；
- critical files（submit/poll/result/comparison/download/state machine）設
  per-file floor；
- Playwright 使用 Chromium JS coverage 或 route-to-requirement mapping
  補充瀏覽器旅程覆蓋，但不與 Vitest line coverage 混為同一百分比。

### 9.3 Requirement coverage

程式碼覆蓋率以外，建立 `qa-requirements.yaml`：

- 每個 DG/CA/UI/LOAD ID 對應至少一個自動 test ID；
- 每次 runner 輸出 passed/failed/not-run；
- hard requirement 的 `not-run` 視同 fail；
- production acceptance 要求 requirement coverage **100%**。

這比單純追求 90% line coverage更能防止「有一半功能完全沒驗」。

## 10. Runner 與 release 整合

新增兩個獨立入口：

```bash
# 無 AWS、每次 pre-push
python scripts/run_local_acceptance.py

# 真正式 URL/AWS；每次部署後
python scripts/run_production_acceptance.py \
  --base-url https://trustforge.hurricanesoft.com.tw \
  --release-id <manifest-release-id> \
  --budget-cap-usd <cap>
```

production runner 必須：

- 先驗 release manifest binding；
- fail-closed 檢查今日 budget；
- 用唯一 run namespace/idempotency keys；
- bounded concurrency 與總 timeout；
- 不讀個人長效 AWS credentials；使用 deployment/QA role；
- 寫 immutable acceptance receipt；
- 任何 hard case failed/not-run 都回 non-zero；
- 不自行 promote、merge、rollback；只把結果交給 release controller 決策。

## 11. 分階段工作與相依性

每張工單上限 12 小時：

| 順序 | 工作 | 估時 | 相依 |
|---:|---|---:|---|
| 1 | QA requirement schema、case registry、artifact schema | 8h | #1093 |
| 2 | production black-box runner：五幣三題型 | 12h | 1 |
| 3 | production multi-angle 5×5、authority/cost settlement | 12h | 1 |
| 4 | source-family coverage/freshness hard gate | 10h | 2 |
| 5 | manual priority + backlog + 10 分鐘 SLA E2E | 10h | 3 |
| 6 | report/evidence/log/download artifact verifier | 8h | 2、3 |
| 7 | Playwright desktop/mobile critical journeys | 12h | 1、6 |
| 8 | backend module/branch/context coverage gates | 8h | 2、3、5 |
| 9 | frontend Vitest + Playwright coverage reporting | 8h | 7 |
| 10 | deployment/acceptance receipt 分離與 release controller 串接 | 10h | 2–9 |
| 11 | full-matrix load/timeout/restart/backpressure | 12h | 3、5、10 |
| 12 | Wiki runbook、QA dashboard、證據保存與告警 | 6h | 10、11 |

工作 2 與 3 可在工作 1 完成後平行；工作 4/5/6 必須先完成才能宣告
competition acceptance。工作 7–9 完成前，UI release 仍需人工 Chrome gate。

## 12. Definition of Done

QA 補齊只有在以下條件全部成立才算完成：

- DG 與 Competition Acceptance runner 分離且都 fail closed；
- 每次 production deployment 自動跑五幣完整矩陣；
- 25 個五方向 jobs 與 5 個 synthesis 在 10 分鐘內完成；
- manual backlog 插隊用真 timestamps 證明；
- required source family coverage 100%；
- 五份 multi-source、五份 hypothesis、五份 comparison 報告內容契約通過；
- Playwright 10 條旅程在 desktop/mobile/breakpoint 全過；
- 真下載與檔案內容驗收通過；
- backend/frontend coverage floors 不低於 baseline；
- hard requirement coverage 100%，無 skipped/not-run；
- acceptance evidence 綁定 release manifest，可下載、可稽核、無 secret；
- 任一 hard gate 失敗時 release receipt 不得標 `production_accepted`。
