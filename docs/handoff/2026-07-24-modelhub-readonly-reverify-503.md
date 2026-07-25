# Issue #503 — ModelHub 唯讀複驗：CTO 交付紀錄

**日期**：2026-07-24｜**Issue 狀態**：OPEN（live acceptance 仍 BLOCKED）｜**分支**：`cto/503-modelhub-readonly-reverify`

## 1. 一句話結論

唯讀複驗的 **evaluator 契約**已完成且健全；**probe collector** 已把 evaluator 橋接至 `ModelHubClient` 的唯讀方法。但在當前 ModelHub 唯讀 API 合約下，probe 必定回 `unverified`／`disabled`——這是外部 API 合約 blocker（引自 2026-07-24 #503 comment），不是 evaluator failure。本任務**不 close #503**。

## 2. 現有整合架構（怎麼互動的）

TrustForge 與 ModelHub 的互動分兩條路徑，本 issue 只涉及第一條：

| 路徑 | 模組 | 性質 | #503 是否觸碰 |
|------|------|------|---------------|
| 唯讀複驗 | `modelhub_readonly_probe.py`、`modelhub_probe_collector.py`（本 PR 新增） | 純 evaluator + 唯讀 client 橋接 | ✅ 本 issue 範圍 |
| 寫入編排 | `modelhub_client.py`（trigger_retrain/poll）、`modelhub_submit.py`、`modelhub_training.py`、CLI `modelhub-train` | state-changing，`automatic_apply: False` | ⛔ 不觸碰 |

唯讀 evaluator（`evaluate_modelhub_readonly_probe`）是個 fail-closed 純函式：caller 餵入「已收集的 observation」，它回傳 `verified`／`unverified`／`disabled` 三態報告與每個 component 的狀態。它**不自行呼叫 ModelHub**、不碰憑證、不做寫入。

## 3. 唯讀複驗實作了什麼

### 3.1 Evaluator（既有，本 PR 強化測試覆蓋）

`modelhub_readonly_probe.py` 的七個 component 全部 fail-closed：

| Component | verified 條件 | unverified 條件 | disabled 條件 |
|-----------|---------------|-----------------|---------------|
| `health` | `health_ok=True` 且有其他證據 | `health_ok` 未證實 / 單獨 200 無其他證據 | `timeout`／`unavailable` |
| `capability` | 含 `{health,list_models,get_model_path}` 且無 state-changing | 缺項 / 非列表 | 含 `upload_artifact`/`trigger_retrain`/`mutate_registry`/`rotate_secret` |
| `identity` | tenant_id + product 對上 | identity 非 dict | tenant/product mismatch |
| `read_access` | 跨租戶＋跨 artifact 讀取皆被擋 | negative checks 非 dict / 不完整 | 任一越權讀取成功 |
| `artifact` | artifact_id + sha256 對上（可選重算吻合） | artifact 非 dict | id mismatch / checksum mismatch / payload 非 bytes / 重算 mismatch |
| `provenance` | id 對上且 `verified=True` | 非 dict / `verified!=True` | id mismatch |
| `mutation_guard` | 無 state change 嘗試 | — | `mutations_attempted` 非空 |

聚合狀態：任一 disabled → `disabled`；否則任一非 verified → `unverified`；全部 verified → `verified`。

### 3.2 Probe collector（本 PR 新增）

`modelhub_probe_collector.py` 把 evaluator 橋接至 `ModelHubClient` 的**唯讀**方法：

- `collect_readonly_observation(client)`：呼叫 `client.health_check()` 與 `client.list_models()`，回傳 observation dict。
  - `health_check()==True` 且 `list_models()` 成功 → `{"health_ok": True, "capabilities": ["health","list_models","get_model_path"]}`
  - `health_check()==False`（client 內部 list_models 拋 ModelHubError）→ `{"unavailable": True}`
  - 拋 ModelHubError → `{"unavailable": True}`
  - **絕不**呼叫 `trigger_retrain`／`poll_training_result`（防回歸測試釘住）
- `run_readonly_probe(client, requirement)`：collector + evaluator 的便利包裝。

關鍵設計決定：collector **不偽造** `identity`／`negative_read_checks`／`artifact`／`provenance` 證據。原因——當前 ModelHub 唯讀合約沒有這些 endpoint（見 §5）。因此 collector 在當前合約下**必定**讓 evaluator 回 `unverified`（服務可達）或 `disabled`（服務不可達）。

## 4. unverified/disabled 狀態如何確保

三層保證：

1. **Evaluator 層**：上述七個 component 全 fail-closed；單獨 `health_ok=True` 不構成 verified（`_health_only` 分支明確降為 unverified）。
2. **Collector 層**：當前合約下 observation 永遠缺四個證據家族 → evaluator 必 unverified/disabled。測試 `test_probe_is_unverified_under_current_modelhub_contract` 釘住此不變量。
3. **候選寫入路徑層**（不在本 PR 改動，僅記錄現況）：`modelhub_submit.py` 產出的所有 current manifest 永遠帶 `automatic_apply: False`、`requires_human_approval: True`；proposal 與 activation 是分開的、需具名人工核准的流程（`#510` 範圍）。wrapper 候選**不會**因為 probe 結果而自動啟用。

> wrapper activation 的 authorization／狀態機強化屬 #510 範圍（harper CISO 已對早期 commit FAIL），本 PR 不觸碰。

## 5. 外部依賴（BLOCKED 原因，非本 PR 可解決）

引自 2026-07-24 #503 comment，ModelHub owner 需提供以下唯讀合約，#503 才能 live acceptance：

1. 既有唯讀憑證 reference（**不** rotation/create，僅引用）。
2. tenant-scoped 唯讀 metadata endpoint，暴露 artifact id / model / product / tenant / SHA-256 / provenance。
3. cross-tenant 與 wrong-artifact 請求回 403/404，且無 metadata 洩漏。
4. download/metadata binding 充分讓 TrustForge 唯讀重算 SHA-256。

在這四項到位前，collector 與 evaluator 都只能誠實回 `unverified`。**這不是 TrustForge 端能修的問題。**

## 6. 測試結果

| 測試檔 | passed | 範圍 |
|--------|--------|------|
| `test_modelhub_readonly_probe.py` | 25 | 原有 5 + 新增 20（每個 component 的 fail-closed 分支、聚合優先序、空 observation） |
| `test_modelhub_probe_collector.py` | 10 | 新檔：observation 收集、transport 錯誤、fail-closed 契約、read-only 不變量 spy |
| ModelHub 全套（client/cli/probe/submit/training） | 238 | 零回歸 |

> 全域 coverage gate（75%）在子範圍下達不到，此為 repo 既有設定，handoff doc 慣例不宣稱全域 coverage 過關。pre-push gate 含全套 backend+frontend，見 §7。

## 7. 不在範圍（明確）

- ⛔ 不寫入 ModelHub、不修復 ModelHub、不部署 ModelHub。
- ⛔ 不做健康宣告（probe 是核對，不是「ModelHub 健康」宣告）。
- ⛔ 不碰 DB / migration / secret。
- ⛔ 不 merge（CEO 權力）。
- ⛔ 不碰 wrapper activation（#510）、不碰 submit 寫入路徑、不碰 web runtime。
- ⛔ 不 close #503（live acceptance 仍 BLOCKED）。

## 8. 接手檢查清單

- [ ] reviewer 具名 + commit-bound attestation。
- [ ] `/codex-review` adversarial gate。
- [ ] 安全／權限相關：harper（CISO）審查——本 PR 純唯讀、純加法、不碰 auth/tenant/secret 路徑，但仍依 issue 驗收走雙審。
- [ ] eye scan：本 PR 無 UI 變更，diff 純 Python + 文件。
- [ ] pre-push gate 全綠後才由 CEO 決定 merge。
