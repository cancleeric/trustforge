# #872: Holder Concentration Entity-Resolution Feasibility Gate — Final Report

- 日期：2026-07-29
- Issue：[#872](https://github.com/cancleeric/trustforge/issues/872)（#748 子工單 D）
- 狀態：**FEASIBILITY GATE COMPLETE**
- 最終結論：**need_paid_source — 免費來源 zero eligible；付費來源 potentially eligible with contract gaps**
- 審查：待 gray (CPO) + harper (CISO) review

> **2026-07-29 remediation:** Provider classification, pricing, licensing, SLA
> and procurement conclusions in this report are superseded by
> `ISSUE-994-HOLDER-ENTITY-RESOLUTION-VENDOR-COST-FEASIBILITY-2026-07-29.md`.
> The authoritative decision is **NO-GO** and the dimension remains `unknown`.
> In particular, do not use the unsupported price estimates in the older D1/D3/D4
> working papers.

## Contents

本報告整合 D1–D4 四個 deliverables 的關鍵發現。完整細節參見：
- **D1**：`docs/reports/ISSUE-872-HOLDER-DATA-LANDSCAPE-2026-07-29.md`
- **D2**：`docs/reports/ISSUE-872-ENTITY-DEDUP-MODEL-2026-07-29.md`
- **D3**：`docs/reports/ISSUE-872-LICENSING-FRESHNESS-LIMITS-2026-07-29.md`
- **D4**：`docs/reports/ISSUE-872-FEASIBILITY-DISPOSITION-2026-07-29.md`

---

## 一、Executive Summary

**Holder concentration 不可在當前條件下產出 TrustForge PIT known dimension records。**

原因可歸結為一條無可繞過的因果鏈：

1. 所有公開免費來源均無 PIT revision pinning、content-hash reproducibility 或
   redistribution-compatible license → **free source_families = 0**
2. Top-N address concentration ≠ holder concentration → 即使有免費 address-level
   data，也無法從 address aggregation 推論 holder concentration
3. 付費來源（Arkham、Chainalysis）覆蓋 BTC + BSC entity data，但授權禁止
   redistribution、無 content-hash reproducibility 承諾 → 需合約 negotiation
4. Content-hash reproducibility 是跨所有 provider 的系統性 gap → 即使簽約，
   PIT evidence integrity gate 仍可能無法通過

**路徑**：開 cost-sensitive issue → negotiate Arkham/Chainalysis contract →
  CEO/CPO/CISO 判定 content-hash workaround acceptance → approve → execute 72h
  data build.

**不阻擋**：Issues B (issuance/supply)、C (control/governance)、E (shadow observation)、F (multi-asset benchmark)。

---

## 二、Disposition by Asset

| Asset | Disposition |
|---|---|
| **BTC** | **need_paid_source** |
| **BNB** | **need_paid_source** |
| **ETH** | **need_paid_source** |

---

## 三、Causal Chain: Why Unknown — Audit Trail

```
Step 1 (D1): 12 providers inventoried
  → 0 free-tier providers meet PIT pinning + content-hash + redistribution gates
  → 2 paid-tier providers (Arkham, Chainalysis) are potentially eligible
  → All other 10 providers: ineligible

Step 2 (D2): Entity resolution requires 4-layer mapping (address → cluster → entity → holder)
  → Each layer has systematic false-positive sources
  → 10 forbidden inference patterns documented
  → Minimum entity-label coverage ≥ 60% required to compute concentration metrics

Step 3 (D3): Licensing + reproducibility assessment
  → Blocker 1: All provider TOS prohibit or restrict redistribution/embedding
  → Blocker 2: No provider offers explicit PIT revision pinning API
  → Blocker 3: No provider guarantees byte-stable content-hash reproducibility

Step 4 (D4): Conclusion
  → Free sources: 0 eligible
  → Paid sources: 2 potentially eligible with contract negotiation
  → Content-hash reproducibility: systemic gap → requires CEO/CPO/CISO policy decision
  → Current state: unknown (dishonest to claim known without entity-resolved data)
```

---

## 四、Key Deliverable Summary

### D1: Data Source Landscape
12 providers evaluated. Disposition summary:
- **eligible_with_gaps**: Arkham, Chainalysis (2)
- **ineligible**: Nansen, Glassnode, CoinMetrics, IntoTheBlock, Dune, Messari, Blockchain.com, Explorer labels (8)
- **unknown**: TRM Labs, Elliptic (2)

### D2: Entity-Dedup Model
Defined address → cluster → entity → holder 4-layer mapping:
- 8 academic/industry literature references
- 7 clustering false-positive sources cataloged
- 10-item forbidden inference master list
- Lost-key verifiability decision tree (cryptographic proof required)
- Cross-chain dedup rules for BTC/WBTC/BTCB and staking derivatives

### D3: Licensing & Freshness
Per-provider 5-dimension compliance matrix:
- Pin revision: ❌ (all free), ❓ (paid — contract clarification needed)
- Content-hash: ❌ (systemic gap across ALL providers)
- Independent rebuild: ❌ (requires same API subscription)
- Freshness SLA: ❌ (no provider offers SLA)
- License allows embed: ❌ (all free), ❓ (paid — TOS negotiation needed)

Proposed stale threshold: 30 days. Conflict resolution policy defined.

### D4: Final Disposition
所有核心資產 disposition = need_paid_source。詳細 causal chain、completion checklist、
cost-sensitive issue draft 參見 D4。

---

## 五、What This Does NOT Block

Issues B, C, E, F 不受影響：
- Issuance predictability、control dispersion、supply verifiability、governance
  capture resistance 四個 shadow dimensions 平行開發不受影響
- holder_concentration = unknown 是已知且合法的 shadow dimension state
- Issue F multi-asset benchmark 可用 4/5 known dimensions 參與，delta 上限 = 0.032 × 4 = ±0.128（仍受 TOTAL_DELTA_CAP = ±0.08 限制）

現行 `data/asset_intrinsic_records.json` 無需變更。holder_concentration 維持 unknown
狀態直至 cost-sensitive issue 完成或 permanent unknown declared。

---

## 六、Next Steps

1. CEO review + gray (CPO) + harper (CISO) review this report
2. CEO 決定是否開 cost-sensitive issue for Arkham/Chainalysis procurement
3. 若開立：合約 negotiation（4–6 weeks）
4. 若合約完成：72h data build（見 D4 §五.2）
5. 若合約 fail 或 content-hash workaround rejected：declare unknown_permanent

---

## 七、Completion Definition Checklist

參照 plan §五：

- [x] D1–D4 全部 deliverables 簽入 `docs/reports/`
- [x] BTC、BNB、ETH disposition 各有明確分類（need_paid_source）
- [ ] Cost-sensitive issue opened（待 CEO 審查後開立）
- [x] Top-address concentration ≠ holder concentration 完整論證
- [x] 所有「未知」結論可追溯至具體 license/pinning/freshness/conflict gap
- [ ] CEO 審查通過（待 gray CPO + harper CISO review）
- [x] 本結論不阻擋 issue B、C、E 進展
