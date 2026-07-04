# TrustForge 前後端分離架構＋遷移計劃

> gray（CPO）撰，老闆拍板方向：SSR零-JS→React+Vite+TS+Tailwind 前後端分離。
> 先 grep `web.py`(3149行)/`schema.py`/`deploy_ec2.sh` 實證現況，非憑空設計。

## 0. 現況實證（grep）

- **後端非 Flask，是純 stdlib `http.server`**（`Handler(BaseHTTPRequestHandler)`），
  路由僅 6 條：`/healthz` `/` `/costs` `/status` `/analyze` `/analyze.json`。
- **已有 JSON**：`/analyze.json`（含 `?type=comparison`）回
  `{version, report(asdict), evidence(to_dict), execution_log}`。**但缺**：
  `trust_components` 聚合（`_aggregate_trust_components`）、雷達
  `aggregate_trust_by_kind()`、`price_provenance` 皆只在 `_render_report`
  內算好直接轉 HTML，**未寫進 JSON payload**（好消息：這些函式輸入都是
  `evidence` 陣列本身既有欄位 kind/trust/trust_components，非新查詢，補齊
  只是把既算好的 dict 塞進 JSON，$0 成本）。
- **只有 HTML、無 JSON**：首頁多幣總覽(`_render_home_overview_cached`)、
  `/status`（版本/連線探測/成本摘要/連接器用量/快取節省/資料鮮度矩陣/最近排程）、
  `/costs`（ledger 明細+model token 表）。
- **世界第一功能核對**：雷達（`_render_trust_radar`，PR#60）已上線但無 JSON 出口；
  **PIT 歷史更關鍵**——`ingestion/cache.py::get_trust_history()`（PR#59）已把
  按日快照寫進去，**但目前完全沒有任何路由讀它**，是「資料已存在、零 UI/API
  消費」的完成度落差，遷移時應一併補上（非既有 SSR 功能，是淨新增）。
- **安全機制**：CSP 只在 `Handler._send()` 一處集中下（所有回應共用），現為
  `default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src ...`，
  無 `script-src`（因為零 JS）。SSRF 防護在 `ingestion/*.py`（白名單 host +
  `safe_fetch.fetch_url` 逐跳驗證），與 web 層無關、**不受此次重構影響**。
  無 CORS header（現況同源）。
- **部署**：`deploy/deploy_ec2.sh`，單一 EC2、systemd 直接 `python3 -m
  trustforge.web` bind port 80（**無 nginx**），S3 只放部署包、DynamoDB 存
  cache/ledger/歷史快照。`fetch_scheduler.py` 排程另跑。

## 1. 框架定案：React+Vite+TS+Tailwind（採 CEO 建議）

| 面向 | React+Vite+TS+TW | SvelteKit |
|---|---|---|
| 生態/元件庫 | shadcn/ui、recharts 等成熟且多，雷達/gauge 圖表現成 | 生態小，圖表庫少，雷達需自寫 SVG |
| 打磨速度（8/1決賽） | 元件庫拉了就能用，UI 迭代快 | 手工多，趕工風險高 |
| 大廠對標 | Nansen/Messari/Etherscan 皆 React 系，評審熟悉度高 | 少見於同類競品 |
| Build/部署 | Vite build 純靜態，$0 runtime | 同樣純靜態，差異不大 |
| 團隊 | 目前無前端專職，React 教學/AI輔助資源最多 | 資源少，除錯成本高 |

**結論：React+Vite+TS+Tailwind 定案**。理由：時間只到 8/1-2，元件庫成熟度與
生態資源決定打磨速度，React 對標大廠也最貼近評審預期。

## 2. API 端點盤點（新增/擴充，皆同源 `/api/*`）

| 端點 | 方法 | 說明 | 現況 |
|---|---|---|---|
| `/api/health` | GET | =`/healthz` 改名 | 有(改名) |
| `/api/overview` | GET | 首頁多幣總覽卡（讀既有 in-memory 現貨，零新I/O） | **新增** |
| `/api/analyze` | GET | =`/analyze.json`，補 `trust_components`/`radar`/`price_provenance` 欄位 | 擴充 |
| `/api/analyze`(comparison) | GET | `?type=comparison` 分支同上補欄位 | 擴充 |
| `/api/status` | GET | `/status` 全頁資料結構化：版本/連線/成本摘要/連接器用量/快取節省/鮮度矩陣/最近排程 | **新增** |
| `/api/costs` | GET | ledger 明細 + model token 表 | **新增** |
| `/api/history` | GET | `?coin=BTC` 讀 `get_trust_history()`，PIT 趨勢——**淨新增功能，非搬遷** | **新增** |

**Schema**：統一信封 `{ok: bool, data?: T, error?: {code, message, retry_href?}}`；
錯誤碼對齊既有語意（429 限流／400 輸入錯／502 服務不可用）。**CORS**：不開放，
同源部署（見 §5），省掉攻擊面與預檢複雜度。

## 3. 前端組件盤點（React 樹）

```
App
├─ Layout(minimal header／完整header, CSP-safe 無 inline)
├─ HomePage: HeroCTA + OverviewCards(多幣) + HowItWorks + ExampleLink
├─ AnalyzePage
│  ├─ QueryConsole(幣種/題型/問題表單)
│  └─ ReportDashboard
│     ├─ ConfidenceGauge  ├─ TrustBreakdown(4分項)
│     ├─ TrustRadar(多維度，可展開證據)  ├─ PriceProvenance
│     ├─ FactsList/InferenceList/KeyBasisList(三階梯)
│     ├─ LimitsAndFlipConditions  ├─ CrossSourceSignal(分歧)
│     ├─ ContrarianList  ├─ EvidenceTable  ├─ CostCard
│     └─ HistoryPitChart（新，讀 `/api/history`）
├─ ComparisonPage：雙欄 ReportDashboard 復用
├─ StatusPage / CostsPage：純表格+卡片
└─ ErrorBoundary/ErrorPage(429/400/502/404)
```
Tailwind + **shadcn/ui**（非自建）：gauge/bar/table/badge 現成，圖表用
recharts（雷達/趨勢線）。深色主題沿用現有 `--tf-*` token 精神。

## 4. 安全/CSP（harper 另審，此為初評）

- CSP 收斂為：`default-src 'self'; script-src 'self'; style-src 'self'
  'unsafe-inline'(Tailwind runtime若需) https://fonts.googleapis.com;
  connect-src 'self'; font-src https://fonts.gstatic.com`——**禁
  `unsafe-eval`、禁外部 script host**，Vite build 出的 hash 檔名天然滿足
  self-only。
- 新攻擊面：前端變成獨立可執行 JS → XSS 面變大（原零-JS 天生免疫 XSS），
  需前端框架自帶跳脫（React 預設跳脫，避免 `dangerouslySetInnerHTML`）。
- 後端 SSRF/#24 資料誠實（has_data/single_source）/限流 三者**全保留不動**，
  只是輸出從 HTML 換 JSON，判斷邏輯零變更。

## 5. 部署（同源、$0 runtime）

**方案：沿用單一 EC2，加 nginx 反向代理**（不用 S3+CloudFront——省下額外
AWS 資源設定/DNS/憑證時間，決賽前更省心）。`nginx`: `/` 服務 Vite build 靜態檔，
`/api/*` proxy_pass 到 `127.0.0.1:8080`（python 後端從 80 移到內部埠）。
同源部署 = 免 CORS。**Credit-safe 確認**：nginx 建置期 `dnf install`（免費，
build.dev-time）、Vite build 在 CI/本機跑（不佔 EC2 runtime），EC2 runtime
只多一個 nginx process，無新增付費資源。

## 6. 分階段遷移（不砸 live）

- **P1（CTO 可執行）**：補齊 §2 API 端點（`/api/*` 新路由，舊 SSR 路由
  原封不動保留 LIVE）+ 前端專案骨架(Vite/TS/Tailwind/shadcn 初始化，指向
  暫時 dev proxy)。
- **P2（CTO 執行，QA 每頁驗收）**：逐頁重建（首頁→分析結果頁→status/costs
  →comparison→錯誤頁），與舊 SSR **平行部署**（新前端走 `/app` 路徑或
  獨立 port 測試），逐頁比對資料一致性（雷達/PIT/資料密度全保留）。
- **P3（CEO+CISO+CPO 三審後 cutover）**：nginx 切換 `/` 指向新前端 build，
  舊 SSR 路由降級為 fallback 一週觀察期後移除；CSP 同步收斂。

## 7. 時程/風險（誠實估）

決賽 8/1-2，今天 7/4，**淨剩 ~4 週**。工程量：3149 行 SSR 邏輯全等價搬遷
（非小改）＋新 PIT 圖表，**中大型工程**，若全職投入 P1+P2 約需 2-2.5 週，
P3 cutover+回歸 QA 再 3-5 天，**風險：時間緊繃、無安全邊際**——建議
**P1+P2 完成即凍結新功能，優先確保決賽當天新前端穩定**，PIT 圖表若來不及
可延後 P3 之後（history API 先上，圖表可事後補，不影響評分核心）。
最大風險非技術難度，是「1004 個既有測試」需同步補前端測試/E2E，人力排擠
決賽其他準備——建議 CTO 執行時**每頁遷移都跑一次既有 SSR 對照截圖**，
避免資料密度/雷達/W2/PIT 這些既有護城功能在重寫中被漏接。
