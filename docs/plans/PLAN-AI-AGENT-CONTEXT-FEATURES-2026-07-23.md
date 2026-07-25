# AI Agent 新手脈絡功能開發計畫

> 日期：2026-07-23  
> 依據：`AI-AGENT-CONTEXT-FEASIBILITY-2026-07-23.md`  
> 狀態：待 CEO 核准後實作  
> 工時政策：每張 implementation issue 上限 12 小時；超過即拆分，不在 issue
> 執行途中默默擴張。

## 1. 目標

為新手提供三組可查證、可互動的加密資產理解能力：

1. 賽道、Layer、上下游依賴與代幣用途卡。
2. 報告內專有名詞自動標註、點擊/鍵盤解釋與風險提示。
3. 同層 observed TPS、TVL、Gas、生態活躍度比較，以及公鏈升級的影響路徑。

本計畫不改變 TrustForge 的產品定位：輸出是可溯源的市場資訊與不確定性，不是
買賣建議或價格預測。

## 2. 實作原則

- **Contract first**：先定 schema、版本與 API，再接來源和 UI。
- **Provenance first**：可變資料均保留來源、取得時間、方法與 freshness。
- **Deterministic first**：分類、詞典與指標正規化優先使用確定性邏輯。
- **Bedrock constrained**：模型只能轉譯既有結構化事實或從 allowlist 選 term。
- **Cache-first serving**：外部來源由 scheduler 抓取；使用者請求只讀快照。
- **Fail-soft honestly**：來源失敗時呈現 stale/missing，不用模型或 `N/A` 補假值。
- **Backward compatible**：新增 API 欄位先 optional；舊快照在 TTL 期間仍可讀。

## 3. 目標資料模型

### 3.1 AssetContext

必要欄位：

- `schema_version`
- `asset`
- `sector`
- `layer`
- `settlement_chain`
- `execution_type`
- `gas_token`
- `token_roles`
- `dependencies`
- `valid_from`
- `fetched_at`
- `sources`

`sector`、`layer`、`token_roles` 使用受控 enum；未知值用 `unknown`，不可猜測。

### 3.2 GlossaryEntry 與 TextAnnotation

`GlossaryEntry`：

- `term_id`
- `canonical_label`
- `aliases`
- `plain_language_definition`
- `risk_note`
- `references`
- `version`

`TextAnnotation`：

- `start`
- `end`
- `term_id`
- `matched_text`
- `source`（`dictionary` 或 `bedrock_allowlist`）

offset 以 Unicode code point 的一致實作為準，API 與 TypeScript 測試需鎖定中英文
與 emoji 案例。

### 3.3 PeerMetricsSnapshot

必要欄位：

- `asset`
- `layer`
- `measured_at`
- `tps_observed`
- `tps_window_seconds`
- `tvl_usd`
- `gas_fee_native`
- `gas_fee_usd`
- `gas_transaction_type`
- `active_addresses_24h`
- `transactions_24h`
- `methodology`
- `source_url`
- `fetched_at`
- `freshness`

數值允許 `null` 表示未知；禁止以 0 代表缺值。

### 3.4 DependencyEdge、UpgradeEvent 與 ImpactPath

- `DependencyEdge`：`upstream`、`downstream`、`relation_type`、有效期間、來源。
- `UpgradeEvent`：鏈、事件、狀態、預定/實際時間、官方來源。
- `ImpactPath`：事件、經過的 edge、可能影響、supporting/contrarian evidence、
  uncertainty。

MVP 只描述「可能影響路徑」，不輸出統計因果結論。

## 4. API 與前端邊界

### 4.1 Analyze API

單幣與 comparison payload 新增 optional：

- `asset_context`
- `term_annotations`
- `risk_notices`

舊 cache payload 缺欄位時，前端不顯示卡片且不得 crash。

### 4.2 Peer comparison API

比較結果新增：

- `peer_group`
- `metrics_a`
- `metrics_b`
- `metric_alignment`
- `ecosystem_activity_breakdown`

`metric_alignment` 必須列出時間偏差、缺值、stale 狀態與不可比較原因。

### 4.3 UI

- `SectorLayerCard`：Layer badge、上下游、Gas token、token roles。
- `AnnotatedText`：安全切割純文字節點並插入 `GlossaryTerm`。
- `PeerMetricsTable`：desktop table、mobile cards、缺值與 freshness。
- `EcoLinkPanel`：事件 → 依賴 → 影響的可展開路徑及 Evidence。

## 5. Issue 拆分與相依性

以下編號是建立順序代號；建立 GitHub Issues 後，以真實 issue number 回填或在
issue 本文交叉引用。每張都包含測試、文件與 `git diff --check`，不把測試另算
成隱藏工時。

| 代號 | Issue | 上限 | Depends on |
|---|---|---:|---|
| A1 / #574 | Asset taxonomy 與 AssetContext schema | 8h | baseline gate #565 |
| A2 / #576 | AssetContext repository 與版本/有效期間查詢 | 8h | #574 |
| A3 / #579 | Analyze API 加入 asset context/risk notices | 8h | #576 |
| A4 / #584 | ARB 全棧幣種啟用與 fixture | 12h | #576、#579 |
| A5 / #585 | Sector/Layer/Token Role 卡片 UI | 10h | #579 |
| G1 / #575 | 單一 glossary catalog 與核心詞彙 | 6h | baseline gate #565 |
| G2 / #578 | 確定性 term annotation engine | 8h | #575 |
| G3 / #583 | Report/API annotations 整合 | 8h | #578 |
| G4 / #588 | AnnotatedText 與可存取 popover UI | 10h | #583 |
| P1 / #577 | PeerMetricsSnapshot schema 與比較口徑 | 8h | #574 |
| P2 / #581 | TVL connector、驗證與 fixture | 10h | #577 |
| P3 / #582 | observed TPS/Gas connector、驗證與 fixture | 12h | #577 |
| P4 / #587 | Peer metrics 正規化、快取與 freshness | 10h | #581、#582 |
| P5 / #589 | Comparison API peer metrics 與 alignment | 10h | #579、#587 |
| P6 / #591 | Peer comparison desktop/mobile UI | 12h | #589 |
| E1 / #580 | DependencyEdge/UpgradeEvent schema 與 catalog | 8h | #576 |
| E2 / #586 | Upgrade event connector 與官方來源驗證 | 10h | #580 |
| E3 / #590 | ImpactPath evaluator 與 uncertainty contract | 12h | #586、#587 |
| E4 / #592 | EcoLink impact path UI | 10h | #590 |
| Q1 / #593 | OpenAPI、跨模組 E2E、安全與 eye scan 收斂 | 12h | #584、#585、#588、#591、#592 |

所有 issue 均低於或等於 12 小時。若 P3、P6、E3、Q1 在 refinement 時無法以
12 小時內完成，必須在實作前拆成兩張，不得帶著超時風險開工。

## 6. 相依圖

```text
#565 baseline gate
  ├─ #574 ─ #576 ─ #579 ─ #584 ───────────────┐
  │                 └──── #585 ────────────────┤
  ├─ #575 ─ #578 ─ #583 ─ #588 ───────────────┤
  └─ #574 ─ #577 ─┬─ #581 ─┐                  │
                  └─ #582 ─┴─ #587 ─ #589 ─ #591
          #576 ─ #580 ─ #586 ─────┘            │
                          #587 ─ #590 ─ #592 ───┤
                                                └─ #593
```

可平行的工作：

- A1 與 G1 在 baseline gate 綠後可平行。
- A2 完成後，A3、E1 可平行。
- P2、P3 可平行。
- A5、G4、P6、E4 分屬不同 UI 區塊，但同時修改共用樣式時需先協調。

## 7. 各階段驗收

### Phase A：Asset Context

- 查詢 ARB 可得到 `Layer 2`、Ethereum settlement、Gas token ETH、治理用途。
- 每個欄位可追到來源與有效時間。
- 未知資產/缺欄位不猜測，UI 誠實顯示未知。
- 現有五幣分析與比較不回歸。

### Phase G：Glossary

- FDV、MC、TVL、Tokenomics、Gas Fee、解鎖賣壓可在生成報告中自動標註。
- 中英文、大小寫、重疊詞與 emoji offset 有 regression tests。
- 鍵盤、觸控、Escape、點外關閉皆可用。
- renderer 不使用未清理的 `dangerouslySetInnerHTML`。

### Phase P：Peer comparison

- 同層資產以相同時間窗與方法比較。
- 理論 TPS 不與 observed TPS 混用。
- stale、missing、不同交易類型或時間偏差過大時阻止誤導比較。
- desktop 與 375×667、390×844 mobile 無橫向溢位。

### Phase E：Eco-Link

- 官方升級事件可沿 dependency edge 形成可解釋 impact path。
- 每條影響附 supporting/contrarian Evidence 與不確定性。
- 無足夠資料時輸出「無法判定」，不宣稱因果。

### Phase Q：收斂

- Backend tests、frontend tests/lint/build、`git diff --check` 全綠。
- OpenAPI 與 TypeScript runtime validators 同步。
- 完成 desktop/mobile eye scan、錯誤態、stale/missing 與 overflow 證據。
- 完成 `/codex-review`，修正全部 finding。
- PR 留下 reviewer attestation、eye-scan evidence 與 final disposition。

## 8. Release 與回滾

- schema/API 欄位以 optional 方式先上線。
- connector 與 UI 各自有 feature flag；關閉後回到既有分析報告。
- scheduler 先 shadow-fetch，確認 freshness、錯誤率與成本後再開 UI。
- 不在同一 PR 同時啟用新 connector、改信任分與改投資風險措辭。
- 生產只走 release workflow；部署後驗證 ARB context、glossary、peer comparison、
  stale fallback 與 impact path，再關閉 milestone。

## 9. 開工條件

1. CEO 核准本計畫。
2. #565 或其替代 baseline 修復使強制 pre-push gate 全綠。
3. 每張 issue 指定 reviewer，並確認前置 issue 已完成。
4. 涉及外部來源、成本、token role 風險措辭的 issue，PR 加入 security/adversarial
   review 區段。
