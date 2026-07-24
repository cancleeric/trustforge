# AI Agent 新手脈絡功能可行性分析

> 日期：2026-07-23  
> 範圍：賽道與層級標籤、Smart Term Glossary、同層比較與生態聯動  
> 結論：三項需求皆可行；應以可溯源的結構化知識與指標資料層為核心，Bedrock
> 僅負責受約束的白話轉譯，不應自行生成可變動的分類或市場指標。

## 1. 執行摘要

TrustForge 已具備 ingestion → trust → agent → report 的完整分析管線、Evidence
溯源模型、React 分析頁、雙幣比較頁，以及可存取的名詞 popover。這些能力足以
承接本需求，不需要重建產品骨架。

真正缺口集中在兩個資料層：

1. **資產脈絡知識層**：幣種、賽道、Layer、結算鏈、Gas token、代幣用途與上下游
   依賴關係。
2. **同類指標快照層**：observed TPS、TVL、Gas、活躍度及其時間、單位、方法與來源。

若只在 prompt 中要求模型回答，會讓 Layer、代幣用途、TPS、TVL 等事實缺乏穩定
schema、有效期間與 provenance，違反 TrustForge「可查證的答案」定位。因此建議
先完成結構化資料契約，再做 UI 與 Agent 敘事。

## 2. 現況盤點

### 2.1 可直接重用

- `src/trustforge/ingestion/`：多來源連接器、快取、新鮮度與失敗降級機制。
- `src/trustforge/schema.py`：Report、Evidence、比較報告與序列化邊界。
- `src/trustforge/agent/orchestrator.py`：TrustedBrief 到報告的編排與 Bedrock 邊界。
- `frontend/src/components/GlossaryTerm.tsx`：已支援點擊開關、Escape、點外關閉及
  ARIA 的名詞說明元件。
- `frontend/src/pages/HelpCenterPage.tsx`：已有靜態 glossary 展示入口。
- `frontend/src/pages/ComparePage.tsx`：已有雙幣選擇、排程、輪詢與雙欄報告骨架。
- `frontend/src/components/AnalysisReportView.tsx`：單幣與比較頁共用的報告渲染。

### 2.2 明確缺口

- `COIN_POOL` 目前只有 BTC、ETH、SOL、BNB、XRP；ARB 會在 schema/API 驗證階段
  被拒絕。
- 現行 comparison 是兩次獨立分析並排，不是 Layer peer metrics 比較。
- 既有鏈上資料以 BTC 為主，尚無跨鏈 TVL、observed TPS、L2 Gas 的統一契約。
- glossary 必須由開發者手動插入 React 元件，生成報告沒有結構化 annotations。
- 尚無 asset dependency graph 或 protocol upgrade event 模型。

## 3. 模組可行性

| 模組 | 可行性 | 主要重用 | 主要新增 | 風險 |
|---|---|---|---|---|
| 賽道與層級標籤卡 | 高 | Report UI、Evidence、快取 | AssetContext、來源與有效期間、卡片 UI | 分類過期、同資產多角色 |
| Smart Term Glossary | 很高 | GlossaryTerm、Help Center | 單一詞典、annotation 契約、renderer | 誤標、詞彙重疊、XSS |
| 同層比較 | 中高 | comparison pipeline、雙欄 UI | PeerMetricsSnapshot、正規化與 freshness | 指標口徑不一致 |
| 生態聯動 | 中 | Evidence、事件來源、Agent 敘事 | DependencyEdge、UpgradeEvent、影響路徑 | 把相關性誤述為因果 |

### 3.1 賽道與層級標籤卡

建議新增結構化 `AssetContext`：

```json
{
  "asset": "ARB",
  "sector": "scaling",
  "layer": "L2",
  "settlement_chain": "Ethereum",
  "execution_type": "optimistic_rollup",
  "gas_token": "ETH",
  "token_roles": ["governance"],
  "dependencies": ["ethereum_settlement", "sequencer", "canonical_bridge"],
  "valid_from": "2026-01-01T00:00:00Z",
  "fetched_at": "2026-07-23T00:00:00Z",
  "sources": []
}
```

UI 可據此產生 Layer badge、上下游卡與代幣用途警示。模型只能把上述欄位轉成
白話，不得自行補上資料庫沒有的分類。

新增 ARB 不能只修改 `COIN_POOL`，還要同步處理：

- 幣種 logo、輸入驗證、問題庫與前後端常數。
- OHLCV/價格來源、排程、快取與 freshness matrix。
- 離線 fixture、API contract、comparison 與壓力測試。

### 3.2 Smart Term Glossary

建議後端輸出文字與 annotations，而非把 HTML 寫進 Report：

```json
{
  "text": "ARB 的 FDV 高於目前市值，需留意代幣解鎖壓力。",
  "annotations": [
    {"start": 6, "end": 9, "term_id": "fdv"},
    {"start": 13, "end": 15, "term_id": "market_cap"},
    {"start": 20, "end": 26, "term_id": "token_unlock"}
  ]
}
```

辨識流程採「確定性詞典優先，Bedrock 補充分類」：

1. 詞典處理 FDV、MC、TVL、Gas Fee、Tokenomics 等已核准詞彙。
2. 最長詞優先，避免 `MC` 等短詞在一般文字中誤判。
3. Bedrock 只能選既有 `term_id`，不能直接發布未知定義。
4. 未知詞只建立待審提案。
5. popover 與 Help Center 共用同一份詞典，避免定義漂移。

報告文字必須先以純文字節點渲染，再按 offset 切割成 React nodes；不得以任意
HTML 字串取代實作，避免 XSS 與 markup 破壞。

### 3.3 同層級橫向比較

建議新增 `PeerMetricsSnapshot`：

```json
{
  "asset": "ARB",
  "layer": "L2",
  "measured_at": "2026-07-23T00:00:00Z",
  "tps_observed": 18.4,
  "tps_window_seconds": 86400,
  "tvl_usd": 2500000000,
  "gas_fee_native": 0.00001,
  "gas_fee_usd": 0.04,
  "active_addresses_24h": 185000,
  "transactions_24h": 1590000,
  "methodology": {},
  "source_url": "",
  "fetched_at": "2026-07-23T00:05:00Z"
}
```

比較口徑必須先固定：

- TPS 比較固定時間窗的 observed TPS，不把理論峰值混入。
- Gas 同時保留原生幣、USD、交易類型與採樣時間。
- TVL 保留來源與方法，不將跨協議重複計算包裝成精確事實。
- 「生態繁榮度」拆成活躍地址、交易數、協議數、開發活動、穩定幣供給等
  可追溯子指標，不由 LLM 自由評分。
- 比較快照需有最大時間偏差；不同日期的數字不能無警示並列。

### 3.4 上下游生態聯動

第一版應定位為「有證據的影響路徑」，不是因果預測：

```text
Ethereum upgrade
  → data availability / blob cost
  → L2 batch posting cost
  → L2 user gas and sequencer economics
  → application activity
```

建議建立：

- `DependencyEdge`：上游、下游、關係類型、有效期間與來源。
- `UpgradeEvent`：鏈、事件時間、事件類型、官方來源與狀態。
- `ImpactPath`：被觸發的關係邊、支撐/反方 Evidence、不確定性聲明。

累積足夠事件前後序列後，才能另行評估 event study；MVP 不宣稱升級造成特定
價格或使用量變化。

## 4. 非功能需求

### 4.1 真實性與溯源

- 所有可變資料必須有 `fetched_at`、`source_url`、methodology 與 freshness。
- 來源失敗時保留舊快照並標 stale，不以 `N/A` 或模型猜測覆蓋。
- UI 明確區分「靜態分類」「即時指標」「Agent 推論」。

### 4.2 安全

- 外部 connector 沿用固定 host allowlist、SSRF-safe fetch、timeout 與大小上限。
- annotation renderer 不接受模型輸出的 HTML。
- 未受信來源文字在 glossary 與卡片中都以純文字呈現。

### 4.3 成本與效能

- 分類與 glossary 以 deterministic lookup 為主，不為每個詞增加一次 LLM 呼叫。
- 指標由 scheduler 抓取並讀 cache，不在使用者請求路徑直接 fan-out。
- comparison 讀同一時間窗快照，避免兩幣各自重複呼叫外部服務。

### 4.4 UX 與無障礙

- hover 不是唯一操作方式；鍵盤與觸控都必須可開啟解釋。
- mobile popover 不得超出 viewport；比較表需有窄螢幕 card fallback。
- 每個風險警示都要說明「事實、影響、限制」，避免形成投資建議。

## 5. 規模與優先順序

| 階段 | 範圍 | 粗估 |
|---|---|---:|
| Phase 0 | 基線修復、契約與 fixture | 1–2 天 |
| Phase 1 | AssetContext、Layer 卡、代幣用途警示 | 4–7 人日 |
| Phase 2 | Glossary annotations 與報告 renderer | 3–5 人日 |
| Phase 3 | Peer metrics ingestion、API 與比較 UI | 10–18 人日 |
| Phase 4 | Dependency graph、upgrade event 與 impact path | 8–15 人日 |

建議先交付 Phase 1、2：對新手的價值高，且不依賴所有外部指標接線完成。Phase 3
再建立可信的同層比較；Phase 4 最後上線，避免用尚未校準的資料做因果敘事。

## 6. 目前品質基線

在本分析開始時的舊本機 `main` 實測：

- Python：2,560 passed、26 failed、8 skipped。
- Frontend lint：通過。
- Frontend TypeScript build：`HermesDashboard` 呼叫 `HermesRightRail` 時缺少
  必填的 `onOpenDivergence`。
- `git diff --check`：通過。

本分支建立前已 fast-forward 到最新 `origin/main`。上述結果應視為同步前基線；
實作計畫需先在最新分支重跑完整品質閘，並將仍存在的基線失敗與本功能回歸分離。

## 7. 最終判定

- **立項建議：通過。**
- **先決條件：**先定義 schema、來源、有效期間與比較口徑。
- **優先交付：**Asset Context＋Glossary。
- **最大風險：**跨鏈數據口徑與新鮮度，而非 UI 或 Bedrock 能力。
- **禁止捷徑：**不得只靠 prompt 生成 Layer、代幣用途、TPS、TVL 或升級影響。
