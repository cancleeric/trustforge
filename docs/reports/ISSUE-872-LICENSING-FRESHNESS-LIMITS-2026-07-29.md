# D3: Licensing, Freshness & Reproducibility Limits

- 日期：2026-07-29
- Issue：[#872](https://github.com/cancleeric/trustforge/issues/872)
- 前置：D1 landscape、D2 entity-dedup model

## 一、Executive Summary

所有 inspectable 的 entity-label 資料源均受下列至少一項限制：
1. **授權禁止 redistribution**（含 aggregated output），使 TrustForge 無法產出
   scope 內的 known dimension records
2. **無 PIT revision pinning**，無法滿足 `as_of` PIT replay 契約要求
3. **無 content-hash reproducibility**，無法滿足 byte-stable 獨立驗證要求

付費來源（Arkham、Chainalysis）可能透過合約 negotiation 解決授權與 PIT pinning
問題，但 content-hash reproducibility 是跨 provider 的系統性 gap。

**若需付費來源滿足 source_families ≥ 2，應開立獨立 cost-sensitive issue。**

## 二、Per-Provider Licensing Assessment

### 2.1 Five-Dimension Compliance Matrix

每 provider 評估五維：**pin revision**、**content-hash verify**、**independent
rebuild**、**freshness SLA**、**license allows embed**。

| Provider | Pin Revision | Content-Hash Verify | Independent Rebuild | Freshness SLA | License Allows Embed | Tier |
|---|---|---|---|---|---|---|
| Arkham | ❓contract | ❌ | ❌ | ❌(no SLA) | ❓contract | Enterprise |
| Chainalysis | ❓contract | ❌ | ❌ | ❌(no SLA) | ❌(strict) | Enterprise |
| Nansen | ❌ | ❌ | ❌ | ❌(no SLA) | ❌ | Enterprise |
| Glassnode | ❌ | ❌ | ❌ | ❌(no SLA) | ❌ | Paid API |
| CoinMetrics | ❌ | ❌ | ❌ | ❌(no SLA) | ❌ | Enterprise |
| IntoTheBlock | ❌ | ❌ | ❌ | ❌(no SLA) | ❓ | Custom |
| Dune | ❌ | ❌ | ❌ | ❌(no SLA) | ❓ | Paid API |
| Messari | N/A | N/A | N/A | N/A | N/A | N/A |
| TRM Labs | ❓ | ❓ | ❌ | ❓ | ❌(strict) | Enterprise |
| Elliptic | ❓ | ❓ | ❌ | ❓ | ❌(strict) | Enterprise |
| Blockchain.com | ❌ | ❌ | ❌ | ❌(no SLA) | ❌ | Free |
| Explorer labels | ❌ | ❌ | ❌ | ❌(no SLA) | ❓ | Free (rate-limited) |

**Legend**：✅ = 滿足、❌ = 不滿足、❓ = 需合約確認 / 文檔不足、N/A = 不適用（無 entity-data product）

### 2.2 Critical Blockers

#### Blocker 1: Redistribution / Embedding License

所有 enterprise-tier entity-label provider 的標準 TOS 均禁止或嚴格限制
redistribution：
- **Chainalysis**：End-user licensing；不允許 raw data 或 derived analytics 嵌入
  third-party product；需合約 amendment
- **Arkham**：TOS §4.2 禁止 unauthorized redistribution of entity data；aggregated
  derivative works clause ambiguous；需 legal review
- **TRM Labs / Elliptic**：Compliance-focused licensing；不允許 non-compliance use
  case 的 data redistribution

TrustForge 的 `holder_concentration` dimension record 包含：
- Source URLs（指向 provider 的原始查詢 endpoint）
- Methodology（引用 provider 的 clustering method）
- Content hash（provider response 的 SHA-256）
- Evidence excerpt（provider 回傳的 entity map with PIT timestamp）

如果 provider 授權禁止在第三方產品中嵌入這些元素，則 dimension record 無法以
known status 簽入 repository。Aggregated Gini/HHI/top-N 指標可能落在 "derived
analytics" exception，但需 provider 合約確認。

**建議**：若開啟 cost-sensitive issue，Arkham 為首選 target（TOS 中 retained
rights clause 較 Chainalysis 寬鬆）；合約 negotiation scope 必須包含：
- TrustForge 在 repository 中儲存 PIT entity map excerpt 的權利
- TrustForge 在 API/UI 中展示 aggregated Gini/HHI/top-N 指標的權利
- Audit trail requirement（content hash + evidence excerpt）

#### Blocker 2: PIT Revision Pinning

無一 provider 提供明確的 `as_of` + `revision_id` 兩段式 PIT query API：
- Arkham：採用 "current + archival snapshots" model，archival snapshots 可能具有
  implicit revision timestamp，但 API 文件未載明查詢語意
- Chainalysis：Reactor 支援 historical investigation view；API KYT 查詢為
  current-state only
- 其他所有 provider：current-state only API

**Workaround 評估**：若 Arkham/Chainalysis API 回傳 response 中包含 block height /
ledger timestamp，TrustForge 可自訂 snapshot + self-archive 機制，將 response
body content-hash + 保存為 evidence excerpt。但此方案：
1. 仍無法獨立第三人重建（需相同 API key + subscription tier）
2. Content hash 只能用於驗證 self-archive 的 integrity，無法驗證 provider 的
   revision consistency
3. 需確認 provider 授權允許 self-archive（可能觸發 TOS 的 data retention 條款）

#### Blocker 3: Content-Hash Reproducibility（系統性 Gap）

這是跨所有 provider 的系統性 gap：無 provider 承諾同一 PIT 查詢產出 byte-stable
response。根本原因：
- Closed-source backend：entity clustering algorithm 可能隨 model update 變更
- Live data pipelines：entity label 可能因新 data 而 retroactively 更新（backfill）
- No revision pinning：無法鎖定 entity model version

**TrustForge 的 content-hash 契約**（`asset_intrinsic.py:54-92`）要求：
- `provenance.content_hash` 為 SHA-256 hex digest（64 hex chars）
- Evidence file bytes 必須 exact-match content_hash
- 這是 PIT integrity 的核心保證

在 provider 不保證 byte-stable response 的前提下，TrustForge 只能 hash self-archived
snapshot。這意味著：
- **content-hash verify** = self-archive integrity check（非 provider consistency）
- **independent rebuild** = 不可行（需相同 API subscription）
- Source family 的 "independence" 仍成立（不同 provider = 不同 methodology），但
  content-hash gate 的語意需 downgrade 為 "self-archived snapshot integrity"

**是否可接受此 downgrade？** 這是 CEO/CPO/CISO review 需判定的 policy question。
若判定不可接受，holder_concentration 維度的 disposition 為 permanent unknown。

## 三、Freshness / Staleness Policy

### 3.1 Proposed Stale Threshold

| 參數 | 提議值 | 理由 |
|---|---|---|
| `valid_until` default | `as_of + 30 days` | 鏈上地址 clustering 變化速率的主要 driver：CEX hot wallet rotation（~weekly）、新 exchange deposit address deployment（daily）、機構錢包重組（monthly）。30 天為保守中位數。 |
| `valid_until` max | `as_of + 90 days` | 超過 90 天，entity map 可能因以下原因顯著偏離 ground truth：major exchange wallet restructure、新 institutional custodian onboarding、治理變更（token unlock / DAO treasury rebalance）、跨鏈橋 upgrade |
| Re-fetch interval | ≤ 30 days for `known`、immaterial for `unknown` | 符合 freshness SLA；在 stale 之前有足夠窗口取得新 snapshot |

### 3.2 Literature Support

- Meiklejohn et al. (2013) 指出 BTC address clustering 的 temporal stability
  取決於 user 的 address reuse 行為；address reuse rate 隨時間下降（Bitcoin Core
  HD wallet adoption），但 cluster identity 的穩定性較高
- Ermilov et al. (2017) 發現 account-model chain 的 clustering accuracy degrade
  在 90-day 窗口因新合約交互與地址創建而需重新校準
- Chainalysis 2023 Crypto Crime Report 指出 major exchange wallet infrastructure
  rotatation 周期約 2–4 weeks

### 3.3 Staleness Decision Matrix

```
fact.age > valid_until (30d default)
├── YES → status = stale → contribution = 0
│   └── Auto-trigger re-fetch via shadow observation pipeline
└── NO → fact remains eligible_at(as_of)

fact has entity label from provider revision R₁ at time T₁,
    but same address has different entity label at T₂ (same provider)
├── Check if T₂ falls within fact.valid_until
│   ├── NO → new evidence post-validity; old fact still valid for PIT(T₁)
│   └── YES → conflicted within validity window → disposition = conflict
│       → contribution = 0; trigger cross-provider resolution
└── T₂ is after fact.valid_until → fact naturally stale → re-fetch
```

## 四、Conflict Resolution Policy

### 4.1 Cross-Provider Entity Label Conflict

若 provider A 與 provider B 對同一地址的 entity label 不一致：

```
address X: Provider A label = "Binance", Provider B label = "Unknown"
├── Label granularity mismatch
│   ├── "Unknown" is absence of label, not contradictory label
│   └── Resolution: accept "Binance" with notation of single-provider-only label
├── Conflicting labels: Provider A = "Binance", Provider B = "Kraken"
│   ├── Resolution: label = CONFLICTED for this address
│   ├── Address removed from both entity's holding aggregation
│   └── Flag as data quality incident
└── Entity agreement rate = agreed_labels / (agreed_labels + conflicted_labels)
    ├── Agreement rate ≥ 80% → gate passed; use agreed-on labels from both providers
    └── Agreement rate < 80% → dimension = conflicted → contribution = 0
```

### 4.2 Intra-Provider Revision Drift

若 provider 在 consecutive revisions 中變更同一地址的 label：

```
Revision R₁: address X = "Binance"
Revision R₂: address X = "Sanctioned Entity" (label removed by provider)
```
- 此為 provider 的 internal data correction / model update
- TrustForge 的 PIT view 只看 `as_of` 時間點對應的 revision
- 若 R₁ 為 `as_of` 對應 revision → 使用 R₁ label（此為 historical fact at PIT）
- 若 R₂ 為 `as_of` 對應 revision 且 R₁ label 已 retracted → 使用 R₂ label
- 不可跨 revision 混合 label（這違反 PIT consistency）

**PIT-consistent principle**：`as_of` 對應的事實是 "T 時間點 provider 宣稱的
entity map"，不是 "回溯校正後的 ground truth"。這符合 TrustForge 的 PIT 哲學：
reproducible historical view，非 omniscient truth。

### 4.3 Conflict Resolution Example Cases

**Case 1: Same-provider revision drift**
- 2026-01-15：Arkham 標記地址 0xABC 為 "Binance"
- 2026-03-01：同一地址被 Arkham retag 為 "Unknown"（可能是 exchange wallet migration）
- TrustForge PIT(as_of=2026-02-01)：使用 2026-01-15 snapshot → "Binance"
- TrustForge PIT(as_of=2026-04-01)：使用 2026-03-01 snapshot → "Unknown"
- 無 conflict；這是正常的 PIT time-slicing

**Case 2: Cross-provider label conflict**
- 2026-02-01：Arkham 標記地址 1A1z... = "Unknown entity"
- 2026-02-01：Chainalysis 標記同一地址 = "Binance"
- 兩者均為 valid as_of=2026-02-01
- Entity agreement: 1 conflicting / 1 total = 0% agreement
- Resolution: address removed from both aggregations；data quality incident filed
- Impact: holder_concentration coverage 下降

## 五、Cost-Sensitive Issue Draft

### 5.1 Trigger

若 D1 結論為「免費來源 zero eligible」，且付費來源 Arkham + Chainalysis 為
source_families ≥ 2 的唯一路徑，則需開立 cost-sensitive issue。

### 5.2 Draft Issue

```
Title: [COST-SENSITIVE] Procure entity-label data access for holder_concentration dimension
Parent: #872
Type: cost-sensitive procurement
Approval: harper (CISO) + gray (CPO) review required

Scope:
- Enterprise API subscription to Arkham Intelligence (primary target)
  or Chainalysis (secondary target)
- Minimum contract scope: single-seat API access with PIT snapshot export rights
- Contract must include:
  1. Right to store PIT entity map excerpts in TrustForge repository
  2. Right to display aggregated Gini/HHI/top-N metrics in product UI
  3. Right to self-archive API responses for audit trail
  4. Clarification on revision pinning mechanism
- Budget range: $15K–$50K/yr (Arkham estimate)
- Timeline: 4–6 weeks (contract review + compliance onboarding)

Outcome:
- If approved and contract signed: D1 disposition upgraded to eligible (2 families)
- If rejected or contract terms unacceptable: holder_concentration = unknown_permanent
```

## 六、Recommendations for D4 Feed-in

1. **Content-hash reproducibility 是最大的方法論 gap**。若此 gap 被判定為
   blocker，holder_concentration 的 disposition 為 unknown_permanent。
2. **付費來源是 source_families ≥ 2 的唯一路徑**。免費來源 zero eligible。
3. **Arkham 為首選付費 target**：覆蓋 BTC + BSC、TOS 較 Chainalysis 寬鬆。
4. **Self-archive + content-hash on snapshot 是合理的 workaround**，但需
   CEO/CPO/CISO 判定是否接受 PIT gate 語意 downgrade。
