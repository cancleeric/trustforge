# D4: Holder Concentration Feasibility Final Disposition

> **2026-07-29 remediation:** The current authoritative disposition is the
> explicit **NO-GO** in
> `ISSUE-994-HOLDER-ENTITY-RESOLUTION-VENDOR-COST-FEASIBILITY-2026-07-29.md`.
> No purchase, trial, provider contact or production integration is authorized.

- 日期：2026-07-29
- Issue：[#872](https://github.com/cancleeric/trustforge/issues/872)
- 前置：D1 landscape、D2 entity-dedup model、D3 licensing/freshness/reproducibility limits
- Parent issue：[#748](https://github.com/cancleeric/trustforge/issues/748)（子工單 D）
- Status：**FEASIBILITY GATE RESULT — DISPOSITION: UNKNOWN (blocked by licensing & reproducibility gaps)**

## 一、Final Disposition by Asset

| Asset | Disposition | Rationale |
|---|---|---|
| **BTC** | **need_paid_source** | 付費來源 Arkham + Chainalysis 均覆蓋 BTC entity data；免費來源 zero eligible；需合約確認 PIT pinning + redistribution 後方可開始 known record 產製 |
| **BNB** | **need_paid_source** | 同上；BSC entity data 為跨 provider 稀缺資源；Arkham 為少數覆蓋 BSC + BTC 雙鏈的 provider |
| **ETH** | **need_paid_source** | ETH entity data 來源最豐富（Arkham、Chainalysis、Nansen、Glassnode 均覆蓋），但 Nansen/Glassnode 無 PIT pinning；付費 Arkham/Chainalysis 仍為必要路徑 |

**額外資產（M1 scope 擴充）均依相同邏輯：免費來源 zero eligible；付費來源視 provider 覆蓋範圍決定。**

## 二、Traceable Causal Chain: Why Unknown

本節提供從 D1 → D2 → D3 → 結論的可追溯因果鏈，確保 future audit 可驗證。

### Step 1: D1 Source Landscape → Zero Free-Tier Eligible (D1 §四)

盤點 12 provider。所有 inspectable 公開免費來源均不滿足：
- PIT revision pinning（無 `as_of` + `revision_id` 兩段式 API）
- Content-hash reproducibility（無 byte-stable response guarantee）
- Redistribution-compatible license（TOS 禁止或嚴格限制）

→ 免費 source_families = **0**（需要 ≥ 2 才能通過 coverage gate）

### Step 2: D2 Entity Resolution → Address ≠ Holder (D2 §一)

Top-N address concentration 不等同 holder concentration。需要四層映射路徑：
`address → cluster → entity → holder`。每層有系統性 false-positive 來源。
直接從 address aggregation 推論 holder concentration 是 invalid inference。
所有 inspectable provider 的免費 tier 僅提供 address-level data，不提供
entity-resolution service。

→ 即使有免費 address-level data，也無法產出 holder concentration known record

### Step 3: D3 Licensing & Reproducibility → Zero Content-Hash Capable Provider (D3 §二)

付費來源中最接近 eligible 的 Arkham 與 Chainalysis 仍有未解 gap：
- **Content-hash reproducibility**：跨所有 provider 的系統性 gap（closed-source
  backend；無 byte-stable response 承諾）
- **Redistribution license**：標準 TOS 禁止或限制 embedding in third-party products；
  需合約 negotiation
- **PIT revision pinning**：API 文件未明載；需合約確認

→ 即使付費 access，content-hash reproducibility gap 仍可能使 provider 無法通過
PIT 契約的 evidence integrity gate

### Step 4: Conclusion → Unknown Disposition (Blocked by Licensing + Reproducibility)

```
免費來源 → ineligible（無 PIT pinning、無 content hash、無 redistribution license）
付費來源 → potentially eligible（但 content-hash reproducibility gap 系統性未解；
            redistribution 需合約 negotiation）
Content-hash gate → CANNOT PASS with current provider landscape
Source families   → 0 (free)、potentially 2 if Arkham + Chainalysis contracts signed
Entity coverage   → BTC + BSC covered by Arkham/Chainalysis (付費)
                   → ETH covered by Arkham/Chainalysis/Nansen (付費)
```

**當前 disposition：unknown。不可產出 holder_concentration known records。
不可補值 0.5、不可使用 address-level aggregation 當作 holder concentration。
不可從新聞標題、社群 estimate 或 LLM 推論補值。**

## 三、Disposition Classification per Dimension Gate

### 3.1 PIT Contract Compliance

| PIT Gate Requirement | Status | Detail |
|---|---|---|
| Revision-pinnable (`as_of` + `revision_id`) | ❌ (所有免費來源) ❓ (付費來源，待合約確認) | D3 §二 Blocker 2 |
| Content-hash-verifiable (byte-stable) | ❌ (系統性 gap，含付費來源) | D3 §二 Blocker 3 |
| Independent rebuild | ❌ (需相同 API subscription) | D3 §二 Blocker 3 |
| Source families ≥ 2 | ❌ (0 free; potentially 2 paid) | D1 §四 |
| Known dimensions ≥ 3/5 | N/A (holder_concentration cannot advance beyond unknown alone) | N/A |
| Redistribution / embed license | ❌ (all free); ❓ (paid, contract needed) | D3 §二 Blocker 1 |

### 3.2 Entity Coverage Gate

| Gate Requirement | Status | Detail |
|---|---|---|
| Entity-labeled supply ≥ 60% circulating | ❓ (unknown until provider access confirmed) | D2 §四.4 |
| Cross-provider entity agreement ≥ 80% | ❓ (unknown until dual-provider access) | D2 §四.4 |
| BSC entity coverage | ❓ (Arkham only among inspectable providers) | D1 §四.3 |
| Lost-key exclusion (provable only) | ✅ (methodology defined; requires entity labels) | D2 §五 |

## 四、What This Means for Issue #748

本 disposition 不阻擋 #748（Asset Structure Score promotion）的整體進程：

1. **holder_concentration 維度維持 unknown**（如現行 `asset_intrinsic_records.json`
   的狀態），delta contribution = 0
2. **其他四個 shadow dimensions 不受影響**：issuance predictability、control
   dispersion、supply verifiability、governance capture resistance 可平行開發
3. **Issue F（多資產 benchmark）可用 holder_concentration = unknown 參與 shadow
   scoring**，這是已知且合法的路徑（plan §六）
4. **若後續 cost-sensitive issue approved 且 Arkham/Chainalysis contract signed**，
   holder_concentration 可透過獨立 PIT record 注入
5. **若 content-hash reproducibility gap 被最終判定為永久 blocker**，
   holder_concentration 維度的 disposition 變為 **unknown_permanent**

## 五、Path to ready_for_evidence

若 CEO 決定推進 holder_concentration，以下為必要步驟：

### 5.1 Immediate: Cost-Sensitive Issue

開立 cost-sensitive issue（見 D3 §五 draft），scope：
- Arkham Enterprise API subscription（primary）+ Chainalysis（secondary，for source_families ≥ 2）
- Contract negotiation on PIT pinning、redistribution rights、self-archive rights
- Budget：**unknown / quote required**；未完成上限明確的 12/24/36-month TCO
  前不得採購

### 5.2 Post-Contract: 72h Data Build Plan Outline

若 contract signed 且 PIT pinning + redistribution confirmed：

**Day 1（Data Acquisition）**
- 取得 Arkham + Chainalysis entity map snapshots for as_of=T (BTC + BSC)
- Content-hash each provider response；save as evidence excerpts
- 計算 cross-provider entity label agreement rate
- 計算 entity-labeled supply / circulating supply coverage ratio

**Day 2（Entity Resolution Pipeline）**
- Apply cross-chain dedup rules (D2 §六)
- Apply lost-key/unspendable exclusion rules (D2 §五)
- Aggregate entity holdings → compute Gini/HHI/top-1/5/10/50/100
- Remove custodian-held supply from numerator (D2 §四.2)

**Day 3（PIT Record Production）**
- Produce `AssetIntrinsicRecord` for BTC、BNB with known holder_concentration dimension
- Produce evidence excerpts with content hash
- Verify against all `asset_intrinsic.py` invariants
- Run pre-push gate (tests + lint + build + data checks)
- PR review + codex-review gate

**Prerequisites**：
- Source families ≥ 2 confirmed
- Content-hash gate workaround confirmed (self-archive + snapshot integrity)
- Entity coverage ≥ 60% threshold met
- Cross-provider agreement ≥ 80% met

## 六、Risks Registered

| Risk | Likelihood | Impact | Mitigation Status |
|---|---|---|---|
| All free sources ineligible | **Confirmed** | High — zero source_families | Honest-zero disposition；不阻擋其他四維 |
| Content-hash reproducibility impossible | **High** — no provider offers byte-stable API | Critical — may be permanent blocker | Flagged for CEO/CPO/CISO policy decision |
| Paid source contracts unacceptable | **Medium** — TOS negotiation | High — permanent unknown | Cost-sensitive issue pre-drafted |
| BSC entity data too sparse for ≥60% coverage | **Unknown** — requires actual data access | High — BNB would remain unknown even after procurement | Flag in cost-sensitive issue Scope |
| Provider stops offering PIT API | Low | Medium — fact becomes stale → re-fetch or archival replay | Staleness policy covers this |
| Regulatory restriction on entity disclosure | Low | Low — TrustForge outputs aggregated metrics only | Not a blocker for initial procurement |
| Label quality degradation over time | Medium — model updates may retroactively change labels | Medium — conflicted facts trigger 0 contribution | PIT time-slicing ensures historical consistency |

## 七、Disposition Declaration

```
FINAL DISPOSITION: need_paid_source

Current state:
- Free sources: 0 eligible (D1)
- Entity model: rigorously defined; address ≠ holder proven (D2)
- Licensing: blocked by redistribution + reproducibility gaps (D3)
- Content-hash reproducibility: systemic gap across all providers (D3)

Blocked by:
1. Absence of any free-tier entity-label source with PIT pinning + content-hash capability
2. Redistribution licensing restrictions on all paid-tier providers
3. No provider guarantees byte-stable content-hash reproducibility

Path to ready_for_evidence:
1. Open cost-sensitive issue for Arkham (+ Chainalysis) procurement
2. Negotiate contract amendments for PIT pinning + redistribution + self-archive
3. Clarify content-hash gate workaround acceptance with CEO/CPO/CISO
4. If all gates cleared: execute 72h data build plan (see §五.2)

Fallback:
- If cost-sensitive issue rejected: holder_concentration = unknown_permanent
- If content-hash workaround rejected: holder_concentration = unknown_permanent
- If contract terms unacceptable: holder_concentration = unknown_permanent
- If entity coverage insufficient post-access: holder_concentration = unknown_permanent

THIS DIMENSION DOES NOT BLOCK ISSUES B, C, E, OR F.
HOLDER_CONCENTRATION = UNKNOWN IS AN ACCEPTABLE SHADOW DIMENSION STATE.
```

## 八、Completion Checklist

- [x] D1 landscape produced（12 providers inventoried；disposition per provider）
- [x] D2 entity-dedup model produced（4-layer mapping；10-item forbidden inference list；8 literature references）
- [x] D3 licensing/freshness limits produced（per-provider 5-dim compliance matrix；stale policy；conflict resolution rules；cost-sensitive issue draft）
- [x] D4 final disposition produced（BTC = need_paid_source；BNB = need_paid_source；ETH = need_paid_source）
- [x] "未知"結論有 D1→D2→D3 可追溯因果鏈
- [x] Top-address concentration ≠ holder concentration 論證完整且有引用
- [x] No numeric estimate or暗示性數值 included
- [x] Parent plan §二不可妥協條件逐條對照無違反
- [ ] CEO (gray CPO + harper CISO) review pending
- [ ] Cost-sensitive issue opened (if CEO approves)

## Appendix A: Parent Plan Gate Compliance

本 disposition 與 Parent plan `PLAN-ISSUE-748` §二不可妥協條件對照：

| 條件 | Compliance |
|---|---|
| 「不明確阻擋其他四維」 | ✅ D4 §四明確宣告 |
| 「需付費資料另開 cost-sensitive issue」 | ✅ D3 §五 draft issue |
| 「不產出即時資料流，只評估可行性」 | ✅ 本單為 feasibility gate，無 code output |
| 「Top-address ≠ holder concentration」 | ✅ D2 §一完整論證 |
| 「未知結論可追溯到具體 gap」 | ✅ D4 §二 Step 1→4 因果鏈 |
| 「不補值、不排序、不假定 asset hierarchy」 | ✅ 所有 disposition 為 unknown 或 need_paid_source |
| 「不修改 production state」 | ✅ 無變更 `asset_intrinsic_records.json` |
