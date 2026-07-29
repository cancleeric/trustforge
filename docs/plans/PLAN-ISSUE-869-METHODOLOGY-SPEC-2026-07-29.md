# Issue #869 五維方法論規格 — Scoped Development Plan

- 作者：gray (CPO)
- 日期：2026-07-29
- 父議題：[#748](https://github.com/cancleeric/trustforge/issues/748)
- 工作分支：`feat/869-methodology`，基底 `e89d4906` (develop)
- 參照規格：[PLAN-ISSUE-748 Master Plan §五-A](https://github.com/cancleeric/trustforge/docs/plans/PLAN-ISSUE-748-ASSET-STRUCTURE-SCORE-PROMOTION-2026-07-29.md)
- 審查需求：CPO (gray) + harper (CISO) + /codex-review + full pre-push
- 預估：8h

---

## §一、範圍 (Scope)

### §一–1 要改的檔案

| 檔案 | 變更性質 | 說明 |
|---|---|---|
| `docs/methodology/ASSET-INTRINSIC-METHODOLOGY.md` | **NEW** | 五維方法論規格文件：逐維定義可重現 measurement、[0,1] normalization 公式、stale 窗、conflict 處理、source-family eligibility、PIT 規則、unknown 規則、禁止推論清單 |
| `src/trustforge/asset_intrinsic.py` | 修改 | 新增 `STALE_WINDOW_DAYS` 常數（初始值 365）、`asset_intrinsic_migration_contract()` 函數，更新 module-level docstring 指到 methodology doc |
| `src/trustforge/asset_intrinsic_shadow.py` | 修改 | `assess_intrinsic_shadow()` 與 `_dimension_output()` 加入 `as_of` 有效性檢查：fact 的 `as_of` 若早於 `assessment_as_of - STALE_WINDOW_DAYS`，視同 stale (delta=0, status=stale)。新增 `validate_forbidden_inferences()` audit 函數，對 provenance.methodology 做模式掃描，拒絕已知禁止推論 |
| `tests/test_asset_intrinsic_metamorphic.py` | **NEW** | Metamorphic tests：相同 facts、不同 asset_id → 相同輸出；symbol/name/issuer 不可影響評分 |
| `tests/test_asset_intrinsic_forbidden_inference.py` | **NEW** | Forbidden-inference tests：price-inferred、popularity-inferred、lost-key estimates、address=entity、Wall Street ownership |
| `tests/test_asset_intrinsic_migration.py` | **NEW** | Backward-compat tests：v1.0.0 records 全線加載、PIT view、shadow assessment 不因 schema 或 methodology 更新而中斷 |
| `data/` | 不變 | 現有 fixture 不變動。新 evidence 在後續 Issue B/C/D 產 |
| `tests/test_asset_intrinsic.py` | 不改 | 現有測試作為守門員保留，只在方法論變更觸發退步時才修改 |
| `tests/test_asset_intrinsic_shadow.py` | 不改 | 現有測試保留；新測試放在對應新檔案 |

### §一–2 明確不改的檔案

- `src/trustforge/calibration.py` — 不涉及校準
- `src/trustforge/trust/scoring.py` — 不接入正式評分（仍為 shadow）
- `src/trustforge/web.py` — 無 API/路由變更
- `src/trustforge/data_contracts.py` — 不 bump schema_version
- `src/trustforge/schema.py` — 不變更 Report dataclass
- `data/asset_intrinsic_records.json` — fixture 語意不變
- 任一前端檔案 — 本 issue 無 UI 變更

---

## §二、Deliverables（逐項可驗證 artifact）

### D1：五維方法論規格文件
**檔案**：`docs/methodology/ASSET-INTRINSIC-METHODOLOGY.md`

逐維包含：
1. **Measurement 定義** — 可重現的操作型定義，含輸入資料型態、取樣窗、最低來源版本 pinning
2. **[0,1] Normalization 公式** — explicit arithmetic，不含 open-ended 自然語言
3. **Stale window** — 預設 365 天；fact 的 `as_of` 超過 STALE_WINDOW_DAYS 時強制 delta=0
4. **Conflict resolution** — 相同 dimension 若有兩個以上 sourced value 且差異 >0.1，標記 CONFLICTED，delta=0
5. **Source-family eligibility** — 至少兩個 `normalized_source_family()` 結果相異的 host；同 parent domain 的子域算同一 family
6. **PIT rules** — 未來 fact、stale fact、expired fact 不進入 eligible set
7. **Unknown rules** — 未知維度貢獻 0；不得補值、補 0.5、或以同業平均值填入
8. **Forbidden inference catalog** — price-inferred、popularity-inferred、lost-key estimates、address=entity、Wall Street/issuer ownership claims，附 pattern 對照表

**驗收方式**：文件存在、章節完整、公式為 closed-form、CPO/CISO 人工審讀。

### D2：Stale window 執行機制
**檔案**：`src/trustforge/asset_intrinsic.py` + `src/trustforge/asset_intrinsic_shadow.py`

修改：
- `asset_intrinsic.py`：新增 `STALE_WINDOW_DAYS: int = 365`
- `asset_intrinsic_shadow.py`：`_dimension_output()` 在判斷 `eligible_at()` 之後，額外檢查 `assessment_as_of - dimension.as_of > timedelta(days=STALE_WINDOW_DAYS)`；若 true，強制設為 stale (delta=0, status="stale", reason_code="stale_fact")
- `STALE_WINDOW_DAYS` 不可由外部未授權變更注入；從模組層級常數讀取

**驗收方式**：測試注入 dimension 的 `as_of` = 367 天前、364 天前，分別驗 stale/eligible 行為。

### D3：Forbidden-inference 稽核函數
**檔案**：`src/trustforge/asset_intrinsic_shadow.py`

新增 `validate_intrinsic_forbidden_inferences(profile: AssetIntrinsicProfile) -> list[str]`：

對 profile 中每個 dimension 的 `provenance.methodology` 做 pattern scan，比對 forbidden catalog：

| 禁止推論 | 正則 pattern |
|---|---|
| Price-inferred | `\b(price|market.cap|trading.volume|exchange.rate)\s*[-–→]\s*(trust|score|confidence)\b` 或反向 |
| Lost-key estimates | `\b(lost|dormant|inaccessible|dead)\s+(coin|key|address|wallet|balance|UTXO)\b` |
| Address = entity | `\baddress\S*\s+(represents?|equals?|maps?\s+to|is\s+(the\s+)?(same\s+as|equivalent\s+to))\s+entity\b` |
| Popularity-inferred | `\b(popular|widely.used|most.traded|adoption.rate)\s*[-–→→]\s*(trust|score|confidence)\b` 或反向 |
| Wall Street ownership | `\b(wall\s+street|institution\S*\s+hold|ETF\s+inflow|fund\s+owns?)\b` |
| Issuer/name/symbol hardcode | `\b(trust\s+BNB|distrust\s+BTC|this\s+coin\s+(is|has)|the\s+issuer\s+(is|has))\b` |

回傳違規清單（可能為空）。若有任何違規，呼叫方 fail-closed（raise ValueError）。

**驗收方式**：test 注入含違規的 methodology 字串，驗證函數正確偵測。

### D4：Migration contract
**檔案**：`src/trustforge/asset_intrinsic.py`

新增 `asset_intrinsic_migration_contract() -> dict` 回傳：
```python
{
    "schema_version": "1.0.0",
    "supported_migrations": [],
    "description": "v1.0.0 records remain valid without migration. Methodology updates are backward-compatible additions to the provenance.methodology free-text field.",
    "breaking_changes": "None at this version."
}
```

必要性：滿足 AC「現有 v1 records 向後相容或有 explicit migration contract」。

**驗收方式**：現有 fixture 全線加載 + PIT view + shadow assessment 不得中斷；migration_contract() 回傳格式符合預期。

### D5：Metamorphic test suite
**檔案**：`tests/test_asset_intrinsic_metamorphic.py`

測試案例：
1. Same 3-KNOWN dimensions (same provenance, values)，分別以 `asset:btc`、`asset:bnb`、`asset:XRP` 和 `asset:anything` 產生 shadow assessment，結果必須完全一致 (total_delta, dimension deltas, gate 狀態)
2. 同一組 dimensions 以不同輸入順序產生相同結果（補充現有 `test_input_order_and_asset_identity_do_not_change_contributions` 但新增 randperm 檢查）
3. 在 view 的 `as_of` 不變的前提下，asset_id 變更不影響 `eligible_dimensions`
4. `assess_intrinsic_shadow()` 不讀取任何外部 asset context（無 import AssetContext）

**驗收方式**：pytest 全部通過。

### D6：Forbidden-inference test suite
**檔案**：`tests/test_asset_intrinsic_forbidden_inference.py`

測試案例：
1. 對已知的合法 methodology 字串（現有 fixture），`validate_intrinsic_forbidden_inferences()` 回傳空清單
2. 對五類禁止推論各一個 variant，驗證偵測
3. 邊界：中文 methodology 含「價格推斷信任」應被偵測（或至少不 false-negative）
4. 邊界：methodology 為空字串（不合法，應在 provenance init 階段拒絕）

**驗收方式**：pytest 全部通過。

### D7：Backward-compat test suite
**檔案**：`tests/test_asset_intrinsic_migration.py`

測試案例：
1. 現有 `asset_intrinsic_records.json` fixture 經過 `load_asset_intrinsic_records()` → `AssetIntrinsicRepository` → `pit_view()` → `assess_intrinsic_shadow()` 全線不拋異常
2. 模擬未來 schema_version 為未知值時 `AssetIntrinsicProfile.__post_init__` 拋出合理錯誤（現行驗收 `!= "1.0.0"` 即 reject）
3. `asset_intrinsic_migration_contract()` 回傳 dict 包含必要 key
4. `STALE_WINDOW_DAYS` 存在且為正整數
5. 現有 BTC 的 `test_real_btc_and_bnb_are_honest_zero` 在 stale window 機制啟用後仍維持 true（BTW: BTC fixture as_of=2026-07-27，測試 as_of=2026-07-28，差距 1 天 << 365）

**驗收方式**：pytest 全部通過。

---

## §三、Test Plan

### §三–1 Metamorphic tests (AC #5)

| ID | 測試 | 檔案 |
|---|---|---|
| M1 | 相同 3-KNOWN dims, 不同 asset_id — shadow output identical | `test_asset_intrinsic_metamorphic.py` |
| M2 | 相同 dims, randperm 順序 — shadow output identical | `test_asset_intrinsic_metamorphic.py` |
| M3 | 相同 dims, 不同 as_of (同 PIT) — eligible_dimensions identical | `test_asset_intrinsic_metamorphic.py` |
| M4 | assess_intrinsic_shadow() 不 import/讀取 AssetContext | `test_asset_intrinsic_metamorphic.py` |

### §三–2 Source-family eligibility tests (AC #3)

| ID | 測試 | 檔案 |
|---|---|---|
| S1 | 2 相同 host 子域 → 算 1 family, gate fail | `test_asset_intrinsic_shadow.py` (擴充) |
| S2 | 2 不同 parent domain → 算 2 families, gate pass | 已有 `test_gate_passes_three_known_two_families_and_sum_equals_total` |
| S3 | `normalized_source_family()` IDNA punycode 安全 | 現有 `test_source_family_normalizes_case_and_trailing_dot` |

### §三–3 Forbidden-inference tests (AC #4)

| ID | 測試 | 檔案 |
|---|---|---|
| F1 | price-inferred language detected | `test_asset_intrinsic_forbidden_inference.py` |
| F2 | lost-key estimates detected | `test_asset_intrinsic_forbidden_inference.py` |
| F3 | address=entity language detected | `test_asset_intrinsic_forbidden_inference.py` |
| F4 | popularity-inferred detected | `test_asset_intrinsic_forbidden_inference.py` |
| F5 | Wall Street ownership claims detected | `test_asset_intrinsic_forbidden_inference.py` |
| F6 | issuer/name/symbol hardcode detected | `test_asset_intrinsic_forbidden_inference.py` |
| F7 | existing legitimate methodology passes clean | `test_asset_intrinsic_forbidden_inference.py` |

### §三–4 Backward-compat tests (AC #6)

| ID | 測試 | 檔案 |
|---|---|---|
| B1 | 現有 fixture 全線加載不中斷 | `test_asset_intrinsic_migration.py` |
| B2 | migration_contract() 格式正確 | `test_asset_intrinsic_migration.py` |
| B3 | 未知 schema_version → reject | `test_asset_intrinsic_migration.py` |
| B4 | STALE_WINDOW_DAYS 存在且 type 正確 | `test_asset_intrinsic_migration.py` |
| B5 | BTC/BNB honest zero 在 stale window 機制下維持 | `test_asset_intrinsic_migration.py` |

### §三–5 Stale window tests (AC #2)

| ID | 測試 | 檔案 |
|---|---|---|
| W1 | `as_of` = 367 天前 → stale, delta=0 | `test_asset_intrinsic_shadow.py`（擴充）或 `test_asset_intrinsic_metamorphic.py` |
| W2 | `as_of` = 364 天前 → eligible, delta != 0 (若 gate pass) | 同上 |
| W3 | 邊界：`as_of` 恰等於 `assessment_as_of - 365 days` → eligible | 同上 |

---

## §四、Acceptance Criteria 對照

| Issue #869 AC | 對應 Deliverable | 驗證方式 |
|---|---|---|
| AC1: 五維 measurement + [0,1] normalization | D1 方法論文件 §逐維定義 | CPO/CISO 人工審讀 |
| AC2: stale 窗、conflict 處理、source-family、PIT、unknown | D1 文件 §規則定義 + D2 stale window code | 文件審讀 + pytest W1-W3 |
| AC3: 公式只讀 verified facts，不讀 symbol/name/issuer | D3 forbidden-inference audit + M4 metamorphic test | pytest F7, M4 |
| AC4: 禁止 price-inferred、popularity-inferred、lost-key、address=entity、Wall Street | D1 文件 §禁止推論清單 + D3 audit 函數 | pytest F1-F7 |
| AC5: 相同事實、不同 asset ID → 相同輸出 | D5 metamorphic tests | pytest M1-M4 |
| AC6: v1 records 向後相容或有 migration contract | D4 migration contract + D7 backward-compat tests | pytest B1-B5 |

---

## §五、風險與模糊點

### R1: Stale window 數值選擇
365 天為初始值，未經資料校準。若觀察到頻繁 stale 觸發，後續可調降（需 CEO 決議）。不阻擋本 issue delivery。

### R2: Forbidden-inference regex 覆蓋
Regex-based pattern scan 無法偵測重新措辭的同等語意推論。這是一個 fail-open 的稽核防線，不是 fail-closed。若子代理需要更強防線，需在 D 或後續 issue 升級為 LLM-based audit（pinned model + prompt hash）。CEO 決議：現階段 regex 足夠。

### R3: Source-family 子域歸屬
`normalized_source_family()` 目前提取 full hostname。兩個不同 hostname 但 same parent domain（例如 `github.com` 和 `raw.githubusercontent.com`）目前的實作會算兩個 independent families，但邏輯上可能應算同一 family。**本次不變更 family 定義**。若觀察到單一 parent domain 操縱 family count，在 D/F 階段才修正。

### R4: Conflict resolution 由誰執行
D1 規格定義 conflict rule (差異 >0.1 → CONFLICTED)，但 D2 不實作 conflict detection in score path。Conflict detection 是 data curation 的責任（Issue C 會實作）。本次只定義規則，不實作 detection。

### R5: 現有 fixture 的 methodology 可能觸發 forbidden-inference scan
BST fixture 的 methodology 字串不含禁止模式，經過 F7 測試後可保證 pass。若後續新增的 fixture methodology 含禁止模式，`validate_intrinsic_forbidden_inferences()` 會 reject，這是設計意圖。

---

## §六、執行順序與相依

```
D1 (方法論文件) → D2/D3/D4 (code) → D5/D6/D7 (tests)
```

D2/D3/D4 可平行執行；D5/D6/D7 必須在對應的 code base 完成後才能開始（相依於被測試的 function）。

D1 為最優先——方法論文件定義後，D2/D3 才有正確的 stale window 常數和 forbidden pattern 清單可實作。

---

## §七、審查門檻

依據計劃 §二，本 issue 屬於 security/judgment-integrity sensitive
（定義可影響使用者對資產本質判斷的評分方法）：

- CPO (gray) review → 本計劃即為 CPO 產出，需 CEO 審查後 approve
- harper (CISO) review → 審查方法論文件 ¬§禁止推論清單、stale window 安全性、source-family manipulation 防護
- /codex-review → 對全部 code delta 執行 adversarial review
- Full pre-push → `.githooks/pre-push` 全綠（含 tests, lint, build, data checks, git diff --check）

---

## §八、完成定義

Issue #869 關閉條件：
1. 七項 deliverables (D1-D7) 全部完成且通過對應測試
2. Pre-push gate 全綠
3. CPO + harper + /codex-review 全 PASS，無 unresolved findings
4. Branch `feat/869-methodology` 合併至 `develop`
5. Pipeline 未因此變更中斷現有 analyze/compare 路徑

一旦本 issue 完成，Issue B (`issuance/supply 通用來源擴充`)、C (`control dispersion/governance capture 方法`)、D (`holder concentration feasibility`) 可平行開工。
