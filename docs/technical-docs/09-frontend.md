# 09 — 前端架構

[← 08 信任演算法詳解 ](08-trust-algorithm.md)[文件首頁 ](README.md)[10 安全 → ](10-security-handover.md)

## 09 — 前端架構

Frontend Architecture · React 19 SPA、元件樹、API Client、型別系統、建置流程

**目錄 **

- [技術棧 ](#overview)

- [目錄結構 ](#directory)

- [核心元件清單 ](#components)

- [頁面路由 ](#pages)

- [API Client 層 ](#api-client)

- [TypeScript 型別系統 ](#types)

- [建置流程 ](#build)

- [前端安全策略 ](#security)

- [開發設定 ](#dev)

- [Hermes UI 模組 ](#hermes-ui)

- [前端測試 ](#testing)

### 1. 技術棧

| 技術 | 版本 | 用途 |
| --- | --- | --- |
| React | 19 | UI 框架 |
| Vite | 8 | 建置工具 + 開發伺服器 |
| TypeScript | 6 | 型別安全 |
| Tailwind CSS | 4 | 樣式框架（utility-first） |
| Recharts | 3 | 圖表庫（信任雷達圖、歷史圖、信心儀表） |
| Vitest | — | 測試框架 |

### 2. 目錄結構

```text

frontend/
├── package.json          # 依賴與 scripts
├── vite.config.ts         # Vite 設定（含 API proxy）
├── tsconfig.json          # TypeScript 設定
├── tailwind.config.ts     # Tailwind 設定
├── index.html             # SPA 入口
├── public/
│   ├── favicon.svg
│   ├── theme-init.js         # 暗色主題初始化（避免閃爍）
│   └── llms.txt
└── src/
    ├── main.tsx               # React root + Router
    ├── App.tsx                # App shell
    ├── pages/                 # 頁面元件（8 頁）
    │   ├── HomePage.tsx           # 多幣總覽儀表板
    │   ├── AnalyzePage.tsx        # 分析報告檢視
    │   ├── ComparePage.tsx        # 雙幣比較
    │   ├── HistoryPage.tsx        # Point-in-time 歷史
    │   ├── StatusPage.tsx         # 系統狀態
    │   ├── CostsPage.tsx          # 成本帳本
    │   ├── HermesDashboard.tsx    # Hermes 自主代理儀表板
    │   ├── AdminPage.tsx          # Runtime Admin
    │   └── NotFoundPage.tsx
    ├── components/            # 可重用 UI 元件（15+ 個）
    │   ├── ConfidenceGauge.tsx
    │   ├── TrustBreakdown.tsx
    │   ├── TrustRadarChart.tsx
    │   ├── TrustHistoryChart.tsx
    │   ├── EvidenceTable.tsx
    │   ├── EvidenceTrailPanel.tsx
    │   ├── CrossSourceSignalPanel.tsx
    │   ├── InsightExplainabilityPanel.tsx
    │   ├── HypothesisLedgerPanel.tsx
    │   ├── OverviewCard.tsx
    │   ├── AnalysisReportView.tsx
    │   ├── PlainLanguageResultSummary.tsx
    │   ├── CoinSelect.tsx
    │   ├── Header.tsx
    │   ├── ThemeToggle.tsx
    │   ├── Badges.tsx
    │   ├── ErrorBoundary.tsx
    │   ├── KeyBasisList.tsx
    │   ├── StatusStates.tsx
    │   ├── GlossaryTerm.tsx
    │   └── BridgeHologramContext.tsx
    ├── hermes/                 # Hermes 自主代理 UI 模組
    │   ├── HermesFirstRun.tsx
    │   ├── HermesOnboarding.tsx
    │   ├── HermesModuleDeck.tsx
    │   ├── HermesLeftRail.tsx
    │   ├── HermesRightRail.tsx
    │   ├── HermesTopBar.tsx
    │   ├── HermesUpgradeShip.tsx
    │   ├── StageBar.tsx
    │   ├── StageDrilldown.tsx
    │   ├── CurrencyGalaxy.tsx
    │   ├── hermesI18n.tsx
    │   └── hermes.css
    └── lib/                    # 共用工具與型別
        ├── apiClient.ts          # Typed fetch wrapper（90s timeout）
        ├── endpoints.ts         # API 端點定義
        ├── types.ts             # TypeScript 型別
        ├── constants.ts
        ├── format.ts
        ├── safeHref.ts          # URL 安全檢查（僅 http/https）
        ├── validators.ts
        ├── theme.ts
        ├── decisionColor.ts
        ├── decisionState.ts
        ├── trustTrend.ts
        ├── sortCoins.ts
        ├── sourceBrand.ts       # 來源品牌/logo/tier 對照
        ├── tierLabel.ts
        ├── manipRisk.ts
        ├── stancePairs.ts
        ├── resultReadiness.ts
        ├── executionLogDownload.ts
        ├── beginnerExperience.ts
        ├── adminConsole.ts
        ├── hermesData.ts
        └── __fixtures__/    # Live API snapshot 測試資料

```

### 3. 核心元件清單

| 元件 | props | 消費 API | 說明 |
| --- | --- | --- | --- |
| `OverviewCard ` | `coin ` | `/api/overview ` | 單幣總覽卡片：trust score、manip score、方向、來源數 |
| `ConfidenceGauge ` | `value, calibrated ` | `/api/analyze `→ report | 信心儀表（半圓弧 + 數值 + 分級標籤） |
| `TrustRadarChart ` | `radar ` | `/api/analyze `→ report.trust_radar | 多維度信任雷達圖（按 kind：news/onchain/social 等） |
| `TrustBreakdown ` | `components ` | `/api/analyze `→ trust_components_aggregate | 信任分量分解條（reputation/corroboration/recency/manipulation） |
| `TrustHistoryChart ` | `coin ` | `/api/history ` | 歷史信任分走勢圖 |
| `EvidenceTable ` | `evidence ` | `/api/analyze `→ evidence | Evidence 表格（來源、時間、信任分、內容） |
| `EvidenceTrailPanel ` | `evidence ` | 同上 | 溯源路徑：claim → source → content reference |
| `CrossSourceSignalPanel ` | `signal ` | `/api/analyze `→ cross_source_signal | 跨源分歧/共識面板 |
| `InsightExplainabilityPanel ` | `insights ` | `/api/analyze `→ insight_labels | 獨特洞察解釋面板 |
| `HypothesisLedgerPanel ` | `hypothesis ` | `/api/analyze `（type=hypothesis） | 假設正反證據帳本 |
| `PlainLanguageResultSummary ` | `report ` | `/api/analyze `→ report | 自然人可讀的結果摘要 |
| `AnalysisReportView ` | `data ` | `/api/analyze ` | 完整分析報告組合（所有子元件組合） |
| `ErrorBoundary ` | `children ` | — | React Error Boundary：捕獲 render 錯誤、顯示 fallback |

### 4. 頁面路由

| 路由 | 頁面 | 主要 API 消費 |
| --- | --- | --- |
| `/ ` | `HermesDashboard ` | `/api/hermes-upgrades `、 `/api/analysis-flow `、 `/api/analysis-journey ` |
| `/home ` | `Navigate ` | redirect 到 `/ ` |
| `/analyze ` | `AnalyzePage ` | `/api/analyze `（完整分析報告檢視） |
| `/compare ` | `ComparePage ` | `/api/analyze?type=comparison `、 `/api/analysis-comparison-question ` |
| `/history ` | `HistoryPage ` | `/api/history `（PIT 歷史走勢） |
| `/status ` | `StatusPage ` | `/api/status `（cache 鮮度、connector 健康） |
| `/costs ` | `CostsPage ` | `/api/costs `（成本帳本儀表板） |
| `/help ` | `HelpCenterPage ` | 說明中心；source route 與 live SPA route 已確認 |
| `/asset-context ` | `AssetContextLookupPage ` | `/api/asset-context `（repo 支援；本輪 production API 仍待部署驗證） |
| `/eco-link ` | `EcoLinkPage ` | `/api/eco-link `（repo 支援；本輪 production API 仍待部署驗證） |
| `/peer-metrics ` | `PeerMetricsPage ` | `/api/peer-metrics `（repo 支援；本輪 production API 仍待部署驗證） |
| `/notifications ` | `NotificationsPage ` | 通知頁；source route 與 live SPA route 已確認 |
| `/settings ` | `SettingsPage ` | 設定頁；刻意不進 Header 主導覽 |
| `/admin ` | `AdminPage ` | `/api/admin/* `；需要 Admin Token |

### 5. API Client 層

**設計紀律： **前端 **不 **直接呼叫 `fetch() `。所有 API 請求統一經 `apiClient.ts `（typed fetch wrapper），用 `endpoints.ts `定義端點常數、 `types.ts `定義回應型別。

#### 5.1 apiClient.ts

```text

**功能：**
- 統一 90 秒 timeout
- 統一 JSON 解析 + 錯誤處理
- 統一 {ok, data, error} 信封解包
- 自動處理 429 rate limit（不自動重試——UI 顯示"請稍候"）

**簽名：**
function apiClient(url: string, options?: RequestInit): Promise

**錯誤處理：**
- HTTP !2xx → throw ApiError(code, message)
- ok === false → throw ApiError(error.code, error.message)
- JSON parse fail → throw ApiError("parse_error")
- Timeout → throw ApiError("timeout")

```

#### 5.2 endpoints.ts

```text

export const API = {
  health:      "/api/health",
  analyze:     "/api/analyze",
  overview:    "/api/overview",
  status:      "/api/status",
  costs:       "/api/costs",
  history:     "/api/history",
  analysisFlow: "/api/analysis-flow",
  analysisSnapshot: "/api/analysis-snapshot",
  analysisJob: "/api/analysis-job",
  analysisQuestion: "/api/analysis-question",
  analysisJourney: "/api/analysis-journey",
  analysisRequeue: "/api/analysis-requeue",
  hermesUpgrades: "/api/hermes-upgrades",
  adminConfig: "/api/admin/config",
  adminAudit:  "/api/admin/audit",
} as const;

```

### 6. TypeScript 型別系統

```text

// types.ts — 關鍵型別

**// API 信封**
type ApiResponse = { ok: true; data: T }
type ApiError = { ok: false; error: { code: string; message: string } }

**// 分析結果**
type AnalyzeData = {
  report: Report;
  evidence: Evidence[];
  execution_log: ExecutionEvent[];
  provenance: RunProvenance;
}

**// Report 核心欄位**
type Report = {
  coin: Coin;
  coin_cn: string;
  direction: "偏多" | "偏空" | "中性" | "不明";
  decision_state: "abstain" | "low_confidence" | "normal";
  calibrated_confidence: number;
  market_judgment: string;
  key_basis: KeyBasis[];
  limits: string[];
  trust_radar: Record;
  trust_components_aggregate: TrustComponents;
  insight_labels: InsightLabel[];
  cross_source_signal: CrossSourceSignal | null;
}

**// Evidence**
type Evidence = {
  source: string;
  claim_id: string;
  trust_score: number;
  trust_components: TrustComponents;
  stance: "bullish" | "bearish" | "neutral";
  content_reference: string;
  fetched_at: string;
}

**// Coin**
type Coin = "BTC" | "ETH" | "SOL" | "BNB" | "XRP";

```

### 7. 建置流程

```text

**開發：**
cd frontend
npm install
npm run dev                          # http://localhost:5173

**本機另起後端時：**
VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev

**Build：**
npm run build                        # tsc -b && vite build → dist/

**預覽 build：**
npm run preview

**部署到 EC2 nginx：**
cd .. && ./deploy/deploy_frontend_nginx.sh

**Lint：**
npm run lint

```

Vite 建置產出純靜態檔（ `dist/ `）：HTML + JS/CSS（hash 檔名）+ SVG assets，不含任何 server-side dependency。nginx 直接 serve 為 SPA（所有路徑 fallback 到 `index.html `）。

### 8. 前端安全策略

| 面向 | 實作 |
| --- | --- |
| **XSS 防護 ** | 全專案禁 `dangerouslySetInnerHTML `。React 預設轉義 JSX 插入。 |
| **SSRF / Open Redirect 防護 ** | 所有外部連結一概先過 `safeHref() `（僅允許 http: / https: scheme，拒絕 javascript:/data: 等） |
| **CSP ** | Content-Security-Policy 限制資源來源。無 inline script/eval。build 產物皆 hash 檔名的 self-host 資源。 |
| **無 key 洩漏 ** | 前端不存任何 AWS key / token / secret。Live token 僅存在於使用者 session。 |
| **Input validation ** | 所有 API 參數（coin、type、q）在 `validators.ts `做前端 check，但 **不信任前端驗證 **——後端獨立驗證。 |

### 9. 開發設定

Vite config（ `vite.config.ts `）設定 API proxy：

- 開發時 `/api/* `請求由 Vite dev server proxy 轉發到 `VITE_API_PROXY_TARGET `

- 預設目標： `http://127.0.0.1:8799 `（本機後端）

- 本機開發可改為 `http://127.0.0.1:8080 `

- Build 產物無 proxy——nginx 在生產環境處理 `/api/* `反向代理

### 10. Hermes UI 模組

`src/hermes/ `目錄包含 Hermes 自主代理的專屬 UI：

| 元件 | 功能 |
| --- | --- |
| `HermesFirstRun ` | 首次引導體驗（3 步驟敘事流程） |
| `HermesOnboarding ` | 新手 onboarding |
| `HermesModuleDeck ` | 模組卡片組（幣種選擇、問題類型、分析深度） |
| `HermesLeftRail / RightRail ` | 左右側欄導航 |
| `HermesTopBar ` | 頂部狀態欄（runtime 開關、成本狀態） |
| `HermesUpgradeShip ` | 升級審核介面（批准/拒絕 Hermes 自我改進建議） |
| `StageBar / StageDrilldown ` | 5 階段執行進度與深入檢視 |
| `CurrencyGalaxy ` | 幣種關係視覺化 |
| `hermesI18n ` | 多語言文案（zh-TW / en） |

### 11. 前端測試

```text

npm run test          # vitest（23+ test files）
npm run test:run      # 單次跑（不 watch）

```

測試覆蓋：組件（ `.test.tsx `）、頁面、lib 工具函數。使用 Vitest + `__fixtures__/ `目錄中的 live API snapshot 作為測試假資料。

[← 07 信任演算法 ](08-trust-algorithm.md)[文件首頁 ](README.md)
TrustForge 技術文件 · 09 前端架構 · v0.18.5
