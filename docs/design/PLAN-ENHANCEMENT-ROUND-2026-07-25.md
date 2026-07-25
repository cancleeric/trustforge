# TrustForge 技術增強輪計劃（2026-07-25）

- 角色：CPO（gray）
- 狀態：**規劃文件，待 CEO 審批後派工**
- Repo：`/Users/yinghaowang/HurricaneSoft/trustforge`，分支 `develop`
- 基準：v0.18.2 just released，`develop = main` 同步
- 測試綠燈：vitest 360/360, pytest 4094, QA 24/24, mobile-geometry ✅
- 範圍：從 30 個 open enhancement issue 選取 Phase 1（3-5 個）

---

## 0. 總體盤點摘要

### 0.1 Issue 全局分布（30 open）

```
ecolink (4):     #580 #586 #590 #593（#593 是整合收尾）
metrics (4):     #581 #582 #587 #589
asset context (3): #574 #576 #579
API (1):         #583
glossary (1):    #575
observability (3): #537 #412 #393
refactor (4):    #408 #409 #420 #421
platform (4):    #423 #424 #386 #385
agent (2):       #383 #（#384 已 close）
replay (1):      #515
repo (1):        #513
architecture (2): #381 #422
```

### 0.2 關鍵相依關閉狀態

| 相依 issue | 狀態 | 影響 |
|---|---|---|
| #565 (baseline test failures) | ✅ CLOSED | 解鎖 #574, #575 |
| #518 (排程 hardcoded path) | ✅ CLOSED | 解鎖 #537 |
| #577 (PeerMetricsSnapshot contract) | ✅ CLOSED | 解鎖 #581, #582 |
| #578 (term annotation engine) | ✅ CLOSED | 解鎖 #583 |
| #403, #405, #407 (ModelProvider prep) | ✅ CLOSED | 解鎖 #408 |
| #410, #411 (event log prep) | ✅ CLOSED | 解鎖 #412 |
| #380 (Architecture Audit) | ❌ OPEN | 封鎖 #381, #385, #386, #383（共 4 張 L 級核心票） |
| #374 (deploy runbook) | ❌ OPEN | 封鎖 #383 |
| #450–#453 (v2 public engine) | ❌ 未完成 | 封鎖 #420 的 long chain |

### 0.3 完全解鎖的 issue（11 張，可即刻開工）

| # | Title | Size | Type | DB? |
|---|---|---|---|---|
| 575 | glossary catalog 與核心詞彙 | S (≤6h) | intelligence | ❌ No |
| 574 | Asset taxonomy 與 AssetContext schema | M (≤8h) | data | ❌ No（contract only） |
| 537 | freshness 全 stale 時標記 degraded | M (≤8h) | observability | ❌ No（issue 明文排除） |
| 515 | reject timezone-unknown replay timestamps | ? | fix/security | ❌ No |
| 513 | restore non-bare primary worktree | S | chore | ❌ No |
| 581 | TVL connector 與嚴格驗證 fixture | M (≤10h) | data | ❌ No |
| 582 | TPS 與 Gas connector | L (≤12h) | data | ❌ No |
| 583 | Report term annotations 契約 | M (≤8h) | delivery | ❌ No |
| 393 | LLM 語意分析重跑五年回填 | L | trust-engine | ❌ No（需 AWS credential, ~$3-5） |
| 408 | ModelProvider composition root | M | refactor | ❌ No |
| 412 | provider/kernel lifecycle evidence | M | observability | ❌ No |

---

## 1. Phase 1 選題邏輯

### 1.1 約束條件（依 CEO 指示）

1. **不需 DB schema 異動**（不需 token）
2. **前端優先**（快速出 PR、視覺可見）
3. **3-5 個 issue**，做完可 develop→main release

### 1.2 實際限制：剩餘 backlog 幾乎全是技術深票

前一輪 UI round（`PLAN-UI-ROUND-2026-07-25.md`）已清掉所有前端 bug 與視覺缺口。目前 30 張 open issue 中 **沒有任何一張是單純 UI bug**——全是 `P1-core` 技術護城河、`P4-engineering` 工程收尾、或 `data-quality` 連接器。這代表 Phase 1 的「前端優先」需要重新定義為：**有使用者可見影響、或能為後續 UI 迭代提供基礎建設**。

### 1.3 Phase 1 選定 4 張

| # | Title | Size | 為什麼選 | 視覺影響 |
|---|---|---|---|---|
| **#575** | glossary catalog 與核心詞彙 | S (≤6h) | 單一檔案、零相依、共用基礎建設 | ✅ popover/Help Center 即時受益 |
| **#574** | Asset taxonomy 與 AssetContext schema | M (≤8h) | 解鎖後續 6 張票（#576→#579→#589, #580→#586→#590）的**唯一瓶頸** | ⚠️ 間接：所有 asset context 功能的前置合約 |
| **#537** | freshness 全 stale 標記 degraded | M (≤8h) | 已有明確 fixture、issue 明文排除 DB | ✅ health/status 回傳機器可讀 degraded |
| **#515** | reject timezone-unknown replay timestamps | M (估 ≤8h) | 安全/資料完整性修復、已有 #502 regression xfail 等轉正 | ❌ 無 UI，但 PIT 正確性是信任引擎的根基 |

**總估時**：≤30h，四張票互不相依，可並行。

---

## 2. 逐票分析

### 2.1 #575 — glossary catalog 與核心詞彙 [≤6h, size:S]

#### 現況
- #565（baseline fix）已 close，本票唯一相依已滿足
- TrustForge 目前沒有統一的 glossary 來源——tooltip/popover/Help Center 各自寫死文案
- 核心詞彙清單已給定：FDV、MC、TVL、Tokenomics、Gas Fee、解鎖賣壓

#### 檔案範圍（預估）
- `trustforge_core/glossary/__init__.py` — 新建 glossary catalog module
- `trustforge_core/glossary/catalog.py` — 單一 versioned catalog（JSON 或 dataclass dict），含 term_id/name/aliases/definition/source/category
- `trustforge_core/glossary/test_catalog.py` — alias 去重、term_id 唯一、schema round-trip 測試
- `frontend/src/lib/glossary.ts`（可選）— 前端 TypeScript mirror，供 popover 使用
- `docs/references/glossary.md`（可選）— 文件同步

#### 相依/順序
- 無相依，可立即開工
- 本票先做完後，#583（Report term annotations）可以接續消費這個 catalog

#### 安全雙審？
- 不需。純資料定義、無 auth/cost/secret。

#### 驗收條件
- [ ] catalog 含至少 6 個核心詞彙（FDV, MC, TVL, Tokenomics, Gas Fee, 解鎖賣壓）
- [ ] 每個詞彙有 term_id、aliases、definition、category
- [ ] 重複 term_id 拒絕、alias 衝突有明確處理
- [ ] 既有 tooltip/popover 文案不改動（本階段不強制遷移，只是先建立 authoritative source）
- [ ] `ruff check` + `pytest` 綠，新增 ≥3 tests

---

### 2.2 #574 — Asset taxonomy 與 AssetContext schema [≤8h, size:M]

#### 現況
- #565 close，唯一相依已滿足
- 這是整條 asset context 相依鏈的**根節點**——完成後可解鎖 #576（repository）→ #579（API context/risk notices）→ #589（comparison API），以及 #580→#586→#590 ecolink 鏈。共解鎖 **6 張票**

#### 檔案範圍（預估）
- `trustforge_core/context/__init__.py` — 新建 context module
- `trustforge_core/context/contracts.py` — AssetContext dataclass、AssetTaxonomy enum、versioned schema
- `trustforge_core/context/test_contracts.py` — schema round-trip、invalid enum 拒絕、none 值處理
- `docs/contracts/asset-context.md` — 更新資料契約文件

#### 關鍵設計決策
- `unknown` 明確標示，不可用空字串或猜測代替
- enum 用 `StrEnum`（Python 3.11+）保證序列化一致性
- version 欄位內建於 schema，供未來向後相容

#### 相依/順序
- 與 #575 並行執行
- **應最先開工**（解鎖最多下游），但本身不依賴任何其他 Phase 1 票
- 完成後立即可接 #576（AssetContext repository）進 Phase 2

#### 安全雙審？
- 不需。純 contract 定義、無 runtime behavior。

#### 驗收條件
- [ ] `AssetTaxonomy` enum 定義完整（coin/token/protocol/chain/nft/unknown 等受控值）
- [ ] `AssetContext` dataclass 含 asset_id, taxonomy, valid_from, valid_until, source, fetched_at, metadata
- [ ] invalid enum 值拋 `ValidationError`（非靜默吞掉）
- [ ] 既有 import 不受影響（本階段不遷移舊 code，只定義新 contract）
- [ ] `ruff check` + `pytest` 綠，新增 ≥5 contract tests

---

### 2.3 #537 — freshness 全 stale 標記 degraded [≤8h, size:M]

#### 現況
- #518（hardcoded path fix）已 close，相依已滿足
- 事故報告指出：排程停擺時 freshness 持續變舊，但 health 表面正常——這是**虛假健康信號**
- Issue 明文排除 DB schema/migration、不新增外部通知、不自動重啟

#### 檔案範圍（預估）
- `trustforge_core/scheduler/freshness.py` — 修改 freshness 評估邏輯，加入 degraded 判定
- `trustforge_core/scheduler/test_freshness.py` — 新的 degraded 邊界測試
- `trustforge_core/api/health.py` 或對應 status 端點 — 回傳 machine-readable `degraded_reason`
- `trustforge_core/scheduler/test_integration.py`（可選）— 整合測試

#### 關鍵設計決策
- **判定門檻**：所有來源最後成功刷新時間 > 2× 排程 interval → `degraded`
- **fail-safe**：觀測資料缺失時**不得**誤報 `healthy`——寧可 false-positive degraded 也不 false-negative healthy
- **輸出格式**：`health` endpoint 加 `degraded: bool` + `degraded_reason: str | null` + `last_success: {source: timestamp}`

#### 相依/順序
- 與 #574, #575 並行執行
- 但**不需跟其他票有任何互動**

#### 安全雙審？
- 不需。純 observability 輸出、不改變任何資料流或 auth。

#### 驗收條件
- [ ] 固定 fixture 超過 2× interval 時 `GET /health` 回 `degraded: true`
- [ ] freshness 正常時維持 `degraded: false`
- [ ] 缺失/部分來源/時鐘邊界有測試覆蓋
- [ ] 不觸發資料抓取、模型呼叫、或 production action
- [ ] `ruff check` + `pytest` 綠

---

### 2.4 #515 — reject timezone-unknown replay timestamps [估 ≤8h]

#### 現況
- Isolated bug：`datetime.fromisoformat(...).timestamp()` 對 **無時區** 的 ISO timestamp 做 host-local 解讀
- 同一份 archive 在不同主機上會得到不同 PIT eligibility——**這是信任引擎的 silent data corruption**
- 已有 #502 xfail regression test（待本修正後轉正）

#### 檔案範圍（預估）
- `trustforge_core/replay/historical_replay.py` — 修改 adapter 邊界的時間解析
- `trustforge_core/replay/test_historical_replay.py` — 新增 UTC 正規化、naive reject、DST 邊界測試
- `trustforge_core/replay/conftest.py`（可選）— 共享 fixture

#### 關鍵設計決策
- **Reject, don't guess**：無時區（naive）→ `ValueError` fail-closed，不可 host-local fallback
- **Normalize**：Z/+HH:MM 正規化為 UTC `datetime`（非 timestamp）
- **不改變 Kernel/scoring 語意**：只在 adapter boundary 修正，核心邏輯不變

#### 相依/順序
- 無相依，與其他三票完全獨立
- **需安全雙審**（harper CISO）——PIT 邊界屬資料完整性

#### 安全雙審？
- ✅ **必須**。這是 replay boundary 的資料完整性修正，harper（CISO）必須審查：
  - naive timestamp rejection 不會漏掉合法 historical data
  - DST 邊界處理正確（+01:00/+02:00 轉換）
  - 極端 valid offset（+14:00, -12:00）正規化後仍可比對

#### 驗收條件
- [ ] `2021-06-30T12:00:00`（naive）→ `ValueError` 拒絕，不進 Evidence binding
- [ ] `2021-06-30T12:00:00Z` → 正常處理
- [ ] `2021-06-30T12:00:00+08:00` → 正規化為 `2021-06-30T04:00:00Z`
- [ ] DST 邊界（`+01:00` ↔ `+02:00`）正規化後宿主時區無關
- [ ] #502 existing xfail → `pytest.mark.skip` 移除（轉正）
- [ ] `ruff check` + `pytest` 綠，完整 regression 通過

---

## 3. 執行順序

```
四票互不相依，理想情況下全部並行開工：

Day 1 上午：
  ├─ #574（Asset taxonomy, 8h）← 最先開工（解鎖最多下游）
  ├─ #575（Glossary catalog, 6h）← 可與 #574 完全並行
  ├─ #537（Freshness degraded, 8h）
  └─ #515（Replay timezone, 8h）← 先送 harper 安全雙審 brief

Day 1 下午～Day 2：
  ├─ #575 先完成 → code review → merge
  ├─ #574 完成 → code review → merge（解鎖 Phase 2 入口 #576）
  ├─ #537 完成 → code review → merge
  └─ #515 完成 + harper 審查 → code review → merge

Day 2 收尾：
  └─ 四票全合併後 → 跑完整 regression → bump version → develop→main release
```

### 順序備註

- **#574 優先級最高**：完成後解鎖整條 asset context 鏈（#576→#579→#589 及 #580→#586→#590），是後續多輪的基礎
- **#575 優先級第二**：最輕量（≤6h），可最快出 PR，能讓 release 有「可見變化」
- **#537 與 #515 無先後**：與其他兩票完全獨立

---

## 4. 安全／成本面標註

| Issue | 安全敏感 | 理由 | 審查者 |
|---|---|---|---|
| #575 | ❌ | 純資料定義，無 runtime behavior | 不需 |
| #574 | ❌ | 純 contract 定義，無 runtime | 不需 |
| #537 | ❌ | 純 observability 輸出，不改資料流 | 不需 |
| #515 | ✅ | PIT boundary 資料完整性，replay 時間判定直接影響信任分數 | **harper（CISO）必須審** |

---

## 5. Phase 1 目標

> 完成這四張票後，**可立即做一個 develop→main release**（v0.18.3 或 v0.19.0）。

### Release 內容：
- ✅ 統一 glossary catalog（6+ 核心詞彙，popover/Help Center 可消費）
- ✅ Asset taxonomy + AssetContext schema 定義（解鎖後續 6 張票）
- ✅ Health endpoint 回傳 degraded 信號（不再有虛假健康）
- ✅ Replay timezone-unknown 修正（PIT 邊界不再 host-dependent）

### Phase 2 預告（不在此計劃範圍）：
- #576（AssetContext repository）← 吃完 #574 後第一張
- #579（Analyze API + asset context）
- #581 + #582（TVL + TPS/Gas connectors, #577 早已 close）

---

## 6. 風險

| 風險 | 等級 | 緩解 |
|---|---|---|
| **#380 遲遲不關**，4 張核心票（#381, #385, #386, #383）持續封鎖 | 🔴 高 | Phase 1 完全避開這些票；若 #380 一直卡，Phase 2-3 會被迫轉向 data connectors + asset context chain |
| **#420 依賴長鏈**（#453→#450→#452），重構 platform 停滯 | 🟡 中 | 同樣不進 Phase 1；Phase 2 可先做 #408（ModelProvider, 已解鎖）為重構暖身 |
| **#393 LLM backfill** 為 P0-critical（8/1 競賽 deadline），但估 2h + 成本 $3-5 + 需 AWS credential | 🔴 高 | **本計劃不入 Phase 1**——#393 需要 CEO 親自確認 AWS credential 可用 + 排專屬時間執行，不適合與其他票混排。建議 CEO 另開一個獨立的 backfill 執行窗口 |
| **#515 安全雙審卡關** | 🟡 中 | 先送 brief 給 harper，可在其他三票開發期間同時審查 |

---

## 附錄：相依圖（簡化）

```
已解鎖（Phase 1 範圍）：
  ┌─ #575 (glossary)     ← #565 ✅
  ├─ #574 (taxonomy)     ← #565 ✅ ──→ Phase 2: #576 → #579 → #589
  │                                    Phase 2: #580 → #586 → #590
  ├─ #537 (degraded)     ← #518 ✅
  └─ #515 (replay)       ← no deps

仍封鎖（不入 Phase 1）：
  ┌─ #380 ❌ ──→ #381, #385, #386, #383
  ├─ #374 ❌ ──→ #383
  └─ #450–#453 ❌ ──→ #420 → #421 → #422 → #423 → #424

Phase 2 候選（等 Phase 1 #574 完成）：
  ┌─ #576 (repository)
  ├─ #581 (TVL)         ← #577 ✅
  ├─ #582 (TPS/Gas)     ← #577 ✅
  └─ #583 (annotations) ← #578 ✅
```
