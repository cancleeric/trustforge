# TrustForge v0.18.1 Release Notes

> 日期：2026-07-24
> Tag: v0.18.1
> 前版: v0.17.2

## 主要功能

### 三軌統一學習架構（#501–#512）

TrustForge 的加密市場信任分析平台新增三軌學習系統：

1. **Question RAG 品質**（#511）
   - 版本化 gold set provenance
   - Reviewer two-of-two 信任錨點（ReviewerAuthorityRegistry + ApprovalStoreSnapshot）
   - 歷史答案永久 `historical_non_evidentiary`，不可升格

2. **分析異常偵測 + 信心校準**（#507–#509）
   - T+1/T+7/T+14 delayed outcome labeler（fixture-based，無 future leakage）
   - Temporal calibration dataset + manifest（train/val/test 時間隔離）
   - 可解釋 anomaly baseline（candidate-only diagnostic，不觸發 activation）

3. **Wrapper 受控升級**（#510，CISO 雙審 PASS）
   - 8 狀態 FSM（diagnostics → proposal → candidate → sandbox → review → activation → monitoring → rollback）
   - Cryptographic binding（SHA-256 9-tuple）
   - Typed AuthenticatedPrincipal + `_canonical_subject`（NFKC + strip + casefold）+ `_assert_no_mixed_script`
   - 離線 rollback（不依賴 ModelHub 在線）
   - ModelHub unverified 時保持 disabled

### Module③ Peer/EcoLink（#651 + #653 + #655）

- Peer metrics 頁（/peer-metrics）：ARB/L2 TPS/TVL/Gas/活躍度
- EcoLink 頁（/eco-link）：影響路徑面板，可能相關非因果
- Illustrative badge 揭露示範資料

### OHLCV Integrity Audit（#478）

唯讀 tamper detection（checksum + symlink + oversized + PIT leakage 防護）。

### 其他

- Glossary/risk_note UI（#644）
- AssetContext lookup（#648）
- Annotated text popover（#627）

## 安全

- **CISO 雙審兩輪**（#510）：首輪抓 H1（自我核准繞過：principal canonicalization 不對稱）→ 修復 → 重審 PASS
- 34 條 CISO 負向安全測試（future event injection、cross-tenant isolation、reviewer forgery、malicious feedback）
- 三軌 feature flag 預設 OFF（pipeline 未接線，不影響 production 行為）

## 測試

- 4146 backend tests passed / 7 skipped / 0 failed
- 339 frontend tests passed
- 86.31% coverage（門檻 75%）
- 16 條真實 AnalysisFlow E2E
- pre-push gate 8/8 全綠

## ModelHub 整合

- 唯讀 probe（#503）：health + capability verified
- 4 component 誠實 unverified（identity/read_access/artifact/provenance）— ModelHub 端需新增唯讀端點
- API key 存於 Hurricane Vault `trustforge/dev/MODELHUB_API_KEY`
