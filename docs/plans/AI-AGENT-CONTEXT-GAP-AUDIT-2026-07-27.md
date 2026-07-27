# AI Agent 新手脈絡三模組缺口稽核與改善文件

> 日期：2026-07-27 12:54 CST（Asia/Taipei）<br>
> 範圍：`Sector & Layer Context Agent`、`Smart Term Glossary`、`Peer Comparison & Eco-Link`<br>
> 目的：把已驗證落地、尚未達標與需要改善的項目明確落文件，避免後續對外說法過滿。

## 1. 稽核結論

三大模組已有可執行原型、API、前端頁面與測試覆蓋；但目前仍屬「ARB / fixture 驗證優先」階段，不應宣稱為全幣種、全即時真資料、完整新手自動導覽或 production-grade 生態影響分析。

| 模組 | 現況 | 未達標重點 | 對外說法 |
|---|---|---|---|
| 賽道與層級標籤卡 | 已有 `/api/asset-context`、`/asset-context`、`SectorLayerCard`；ARB 可回 Layer 2 / Ethereum / ETH gas / governance / dependencies | 完整資料主要只有 ARB；報告頁內尚未看到完整 context card 主流程呈現；白話說明仍偏欄位摘要 | 已具備 ARB 新手脈絡查詢原型，不是全幣種自動理解系統 |
| Smart Term Glossary | 已有後端 glossary catalog、term annotation、前端 `AnnotatedText` / popover / help center | 後端目前主要標註 `market_judgment`；不是整份報告所有段落皆後端標註；前後端 glossary catalog 仍為雙份鏡射 | 已具備核心詞彙標註與風險提示，不應說所有術語全自動覆蓋 |
| Peer Comparison & Eco-Link | 已有 `/api/peer-metrics`、`/api/eco-link`、peer table、EcoLink panel | Peer metrics / EcoLink 明確是 `illustrative: true` fixture；EcoLink 尚缺 supporting / contrarian Evidence 陣列；尚未接 live data / scheduler 到產品主流程 | 已具備同層比較與生態聯動展示原型，不是即時真實市場觀測 |

## 2. 已驗證落地證據

### 2.1 Asset Context / Sector & Layer

- Contract：`src/trustforge/asset_context.py:64-81` 定義 `AssetContext`，包含 `sector`、`layer`、`token_role`、`ecosystem`、`parent_asset_id`、`settlement_chain`、`gas_token`、`dependencies`。
- Fixture：`data/asset_context_records.json:5-17` 收錄 ARB：`sector=l2`、`layer=layer_2`、`token_role=governance`、`ecosystem=ethereum`、`gas_token=ETH`、`dependencies=[ethereum_settlement, sequencer, canonical_bridge]`。
- API：`src/trustforge/web.py:5072-5100` 實作 `GET /api/asset-context?symbol=ARB`，查無資料回 `asset_context: null`，不猜測。
- UI：`frontend/src/pages/AssetContextLookupPage.tsx:10-15` 說明這是新手脈絡查詢小工具，且目前 ARB 有完整 L2 脈絡資料。
- UI Card：`frontend/src/components/SectorLayerCard.tsx:70-137` 顯示 Layer badge、settlement chain、gas token、token role、ecosystem、dependencies 與白話 summary。
- 測試：`tests/test_asset_context_api.py:36-50` 驗證 ARB 回傳 settlement chain、gas token 與 dependencies；`frontend/src/components/SectorLayerCard.test.tsx:29-50` 驗證 Layer 2 badge 與手續費 glossary risk note。

### 2.2 Smart Term Glossary

- Catalog：`src/trustforge/glossary.py:62-119` 收錄 FDV、MC、TVL、Tokenomics、Gas Fee、解鎖賣壓與 risk note。
- Annotation engine：`src/trustforge/term_annotations.py:30-66` 對文字做 deterministic phrase matching，輸出 `term_id`、`matched_text`、offset 與 glossary link。
- Report integration：`src/trustforge/agent/orchestrator.py:1348-1370` 對 `market_judgment` 注入 `term_annotations`。
- Frontend catalog：`frontend/src/lib/glossaryCatalog.ts:64-122` 鏡射核心詞彙與風險提示。
- UI renderer：`frontend/src/components/AnnotatedText.tsx:11-28` 將命中的詞切成 `GlossaryTerm` popover。
- UI accessibility：`frontend/src/index.css:110-126` 定義 glossary trigger 與 popover 樣式；`frontend/src/components/AnnotatedText.test.tsx:8-20` 驗證可點開 popover。

### 2.3 Peer Comparison & Eco-Link

- Peer contract：`src/trustforge/peer_metrics.py:44-92` 定義 `PeerMetricsSnapshot`，包含 `observed_tps`、`tvl`、`gas_fee`、`activity_breakdown` 與時間窗。
- Peer honesty guard：`src/trustforge/peer_metrics_repository.py:1-7` 明確說明資料來自 illustrative fixture；`source` 必須為 `fixture://`。
- Peer API：`src/trustforge/web.py:5124-5180` 實作 `GET /api/peer-metrics?asset=asset:arb`，回傳本體 snapshot 與 peers，並揭露 `illustrative: true`。
- Peer UI：`frontend/src/components/PeerComparisonTable.tsx:6-9` 說明 desktop table / mobile cards，缺值不補 0。
- EcoLink contract：`src/trustforge/ecolink.py:47-134` 定義 `DependencyEdge` 與 `UpgradeEvent`；官方 URL host allowlist 在 `src/trustforge/ecolink.py:12-20`。
- EcoLink honesty guard：`src/trustforge/ecolink_repository.py:11-17` 明確禁止將 impact path 說成因果；只能說「可能相關」。
- EcoLink API：`src/trustforge/web.py:5221-5270` 實作 `GET /api/eco-link?asset=asset:arb`，資料不足回 `insufficient_data`，有路徑回 `possible_relation`。
- EcoLink UI：`frontend/src/components/EcoLinkImpactPanel.tsx:6-9` 明確禁止「導致／因此」等因果字眼，並顯示 official source link。

## 3. 尚未達標與改善清單

### G-A：Asset Context 覆蓋與主流程整合不足

| 項目 | 問題 | 證據 | 改善方向 | 優先 |
|---|---|---|---|---|
| A-1 全幣種脈絡覆蓋不足 | 目前完整 fixture 只看到 ARB；`BTC` 查詢測試預期回 `null`，代表不能說全幣種自動定位 | `data/asset_context_records.json:1-44` 僅 ARB 記錄；`tests/test_asset_context_api.py:53-58` 驗證 BTC 回 null | 補 `BTC/ETH/SOL/BNB/XRP/OP/MATIC` 的受控 asset context fixture，未知欄位保留 `unknown`，每筆附 source / valid time | P0 |
| A-2 主分析報告沒有完整新手卡片體驗 | 後端可在 `_public_report_dict()` 補 `asset_context` / `risk_notices`，但前端主報告是否把 SectorLayerCard 放在分析結果主路徑尚未驗證到 | `src/trustforge/web.py:5317-5330` 只做公開 payload enrichment；目前檢查到的 `SectorLayerCard` 主要接在 `/asset-context` | 在 `AnalysisReportView` 或 report header 加「資產脈絡」區塊，讓使用者查 `$ARB` 報告時直接看到 `[Layer 2]` 卡，而非另開工具頁 | P0 |
| A-3 白話關聯說明偏欄位摘要 | `relationSummary()` 目前只依 settlement chain 組一句，未涵蓋「以太坊熱度、升級、手續費機制」這種更完整上下游敘事 | `frontend/src/components/SectorLayerCard.tsx:51-58` | 增加 `plain_language_context` 或依 layer/token role 組合出更完整的初心者說明；所有文案需受控，不能由 LLM 即興捏造 | P1 |
| A-4 Risk notice 語言偏工程英文 | `_risk_notices_for_context()` 回英文 message，對中文新手不夠友善 | `src/trustforge/web.py:5292-5314` | 改為 locale-aware 或前端 i18n mapping；治理代幣提示應明確寫「ARB 主要是治理代幣，手續費仍用 ETH」 | P1 |

### G-B：Glossary 覆蓋與資料源一致性不足

| 項目 | 問題 | 證據 | 改善方向 | 優先 |
|---|---|---|---|---|
| G-1 後端標註範圍不足 | 後端只對 `market_judgment` 注入 annotations；事實、推論、關鍵依據、限制、could_flip 等段落未見統一後端標註 | `src/trustforge/agent/orchestrator.py:1348-1351` | 擴充 Report annotations 為 section-aware，例如 `field`, `start`, `end`，涵蓋 `facts/inferences/key_basis/limits/could_flip` | P0 |
| G-2 前後端 glossary catalog 雙份維護 | 後端 `glossary.py` 與前端 `glossaryCatalog.ts` 各自維護，未看到產生器或同步檢查 | `src/trustforge/glossary.py:62-119`、`frontend/src/lib/glossaryCatalog.ts:64-122` | 建立 build-time export / generated TS catalog，或加同步測試比對 term_id、aliases、risk_note | P0 |
| G-3 詞彙覆蓋仍是核心六詞 | 已有 FDV、MC、TVL、Tokenomics、Gas Fee、解鎖賣壓；但新手常見詞如 Rollup、Bridge、Sequencer、Settlement、Governance token、Unlock schedule 尚未完整納入核心金融 / L2 glossary | `src/trustforge/glossary.py:62-119` | 補 L2 / DeFi beginner glossary，並區分 report/popover/help center audience | P1 |
| G-4 自動超連結與 hover/click 體驗需做真瀏覽器 eye scan | 單元測試有 popover，但尚未看到本輪 desktop/mobile 真瀏覽器驗收記錄 | `frontend/src/components/AnnotatedText.test.tsx:8-50` 只是 jsdom unit test | 用 Playwright 或 browser eye scan 驗證：hover/click、Esc、外點關閉、手機不溢位、長詞不破版 | P1 |

### G-C：Peer Comparison / Eco-Link 尚屬 illustrative prototype

| 項目 | 問題 | 證據 | 改善方向 | 優先 |
|---|---|---|---|---|
| P-1 Peer metrics 不是 live data | Repository 明確宣告所有 snapshots 來自 illustrative fixture，API 回 `illustrative: true` | `src/trustforge/peer_metrics_repository.py:1-7`、`src/trustforge/web.py:5139-5142` | 接入真實資料來源或 scheduler cache；保留 fixture fallback，但 UI 必須清楚分辨「示範」與「真實觀測」 | P0 |
| P-2 MATIC TPS 缺值導致比較不完整 | fixture 中 `asset:matic` 的 `observed_tps.value` 為 null，會觸發不可比較 | `data/peer_metrics_snapshots.json:101-108` | 補齊同時間窗、同方法、同 source 的 MATIC TPS，或 UI 明確說「TPS 缺資料，故不可比較」 | P1 |
| P-3 Comparison 主流程未完全整合 | 現有 `/peer-metrics` 是獨立頁；`ComparePage` 註解說不掛在雙幣分析表單 | `frontend/src/pages/ComparePage.tsx:208-215`、`frontend/src/pages/PeerMetricsPage.tsx:9-14` | 在正式 compare result 加入「同層比較」摘要卡；查不到同層資料時顯示誠實 fallback | P1 |
| E-1 EcoLink 不是因果，也不是完整 evidence graph | Repository 明確說 ImpactPath 是 correlation，不是 causal claim；目前 API 回 impact_paths，但沒有 supporting / contrarian Evidence 陣列 | `src/trustforge/ecolink_repository.py:11-17`、`src/trustforge/web.py:5225-5232` | 補 `supporting_evidence[]`、`contrarian_evidence[]`、`evidence_source_url`、`observed_at`，並維持「可能相關」措辭 | P0 |
| E-2 EcoLink fixture source 可能像官方 URL，但仍是示範資料 | API 揭露 `illustrative: true`，資料不是即時抓取；若對外 demo 沒講清楚會被誤認為真實監控 | `src/trustforge/web.py:5237-5240`、`src/trustforge/ecolink_repository.py:19-24` | UI badge 與文件需加粗「示範資料」，正式模式需標示 fetched_at / freshness / stale fallback | P0 |
| E-3 上下游連動尚未多跳或完整路徑說明到產品敘事 | API 實測目前 ARB 回 `asset:arb → asset:eth` 單一路徑，未呈現多跳依賴或對上層應用影響 | `src/trustforge/web.py:5266-5270` 回傳 impact paths；目前 fixture 只覆蓋有限 path | 補 bridge / sequencer / settlement / governance 多類 dependency path，並在 UI 顯示「路徑理由」與「資料不足」 | P2 |

### G-D：交付治理與驗收紀錄不足

| 項目 | 問題 | 證據 | 改善方向 | 優先 |
|---|---|---|---|---|
| Q-1 原計畫要求 Phase Q 收斂，但目前未在單一文件記錄達標/未達標 | 原計畫列出 Phase Q：backend tests、frontend tests/lint/build、OpenAPI、eye scan、codex-review、reviewer attestation | `docs/plans/PLAN-AI-AGENT-CONTEXT-FEATURES-2026-07-23.md:218-224` | 建立每次缺口稽核文件（本文件）與後續 PR checklist，逐項更新狀態 | P0 |
| Q-2 本次檢查有 targeted tests 與 frontend build，但尚未跑完整 pre-push | 本次只針對三模組跑後端 93 tests、前端 62 tests 與 build；未跑完整 `.githooks/pre-push` | 本文件「驗證紀錄」 | 若要宣稱 release-ready，必須跑完整 `.githooks/pre-push`，並在 PR 或 release note 綁定 commit SHA | P0 |
| Q-3 目前工作樹有未追蹤 `uv.lock`，本文件不應混入 | `git status` 顯示 `?? uv.lock`，不是本次文件內容 | `git status --short --branch` | 本文件 commit 只納入 docs 檔，不碰 `uv.lock`；後續另行判斷是否應納管 | P1 |

## 4. 建議改善順序

### P0：先把「不能對外過度宣稱」的缺口補齊

1. 補 AssetContext 覆蓋：至少 `BTC/ETH/SOL/BNB/XRP/ARB/OP/MATIC`。
2. 主分析報告接入 `SectorLayerCard` 或等價的資產脈絡摘要。
3. 建立 glossary 前後端同步機制，避免雙份 catalog 漂移。
4. 擴充後端 section-aware annotations，不只標 `market_judgment`。
5. Peer / EcoLink UI 與 docs 加強 `illustrative` 標示；正式資料未接前，不准宣稱 live。
6. EcoLink 增加 supporting / contrarian Evidence 結構。
7. 跑完整 `.githooks/pre-push` 後才能把本模組列為 release-ready。

### P1：提高新手理解品質

1. AssetContext 增加白話敘事欄位或受控文案模板。
2. 新增 L2 / DeFi beginner glossary：Rollup、Bridge、Sequencer、Settlement、Governance token、Unlock schedule。
3. `/compare` 或 analysis result 顯示同層比較摘要。
4. 補 browser / mobile eye scan：375×667、390×844、desktop。

### P2：從 demo prototype 升級為產品化能力

1. Peer metrics 接 live / scheduled cache；保留 fixture 只作 demo fallback。
2. EcoLink 接官方來源 freshness / stale guard / 多跳 dependency path。
3. 建立 OpenAPI 與 TypeScript validators 自動同步驗證。
4. 將缺口追蹤拆成 issue / PR checklist，避免只停在文件。

## 5. 本次驗證紀錄

### 後端 targeted tests

```text
uv run --extra dev pytest \
  tests/test_asset_context_api.py \
  tests/test_glossary_catalog.py \
  tests/test_term_annotations.py \
  tests/test_peer_metrics_contract.py \
  tests/test_peer_metrics_repository.py \
  tests/test_peer_metrics_api.py \
  tests/test_ecolink_contract.py \
  tests/test_ecolink_repository.py \
  tests/test_ecolink_api.py \
  --no-cov

93 passed in 13.88s
```

### 前端 targeted tests

本次前端 targeted tests 合計 **62 passed**。

```text
npx vitest run \
  src/pages/AssetContextLookupPage.test.tsx \
  src/pages/PeerMetricsPage.test.tsx \
  src/pages/EcoLinkPage.test.tsx \
  src/components/AnnotatedText.test.tsx \
  src/lib/glossaryCatalog.test.ts \
  --testTimeout 20000

41 passed
```

```text
npx vitest run \
  src/components/SectorLayerCard.test.tsx \
  src/components/PeerComparisonTable.test.tsx \
  src/components/EcoLinkImpactPanel.test.tsx \
  --testTimeout 20000

21 passed
```

### Frontend build

```text
npm run build

✓ built in 7.80s
```

備註：Vite 回報 chunk size warning，非本次三模組功能錯誤；如要 release hardening，可另開 performance/code-splitting issue。

## 6. 對外可用措辭

建議用：

> TrustForge 已具備 ARB 新手脈絡卡、核心名詞標註與風險提示、同層比較與 EcoLink 影響路徑的可驗證原型。現階段 Peer Metrics 與 EcoLink 仍為 illustrative fixture，尚未宣稱全幣種、全即時真資料覆蓋；下一階段會補全資產脈絡覆蓋、主報告整合、live metrics 與 evidence-backed EcoLink。

避免用：

- 「已完整支援所有代幣」
- 「已即時追蹤所有 Layer 1 / Layer 2」
- 「EcoLink 可證明升級導致價格或生態變化」
- 「所有報告術語都已全自動標註」
