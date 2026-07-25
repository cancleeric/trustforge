# 模組③ 同層橫向比對（Peer Comparison）+ 生態聯動（Eco-Link）— fixture-based 開發計劃（2026-07-24）

owner: gray（CPO）
base: `develop`
狀態：規劃中（本文件不執行實作；所有 PR 皆 base/打回 `develop`）
CEO 定調：本輪只做「可 demo 的 fixture-based API + UI」，不做真實跨鏈爬取；fixture 資料需明確標示 illustrative；誠實鐵則見下。

---

## 0. 動工前查證（佐證，非臆測）

| 檔案 | 行數 | 現況 |
|------|------|------|
| `src/trustforge/peer_metrics.py` | 135 | 純資料契約：`MetricValue`（value/unit/method/source，method ∈ observed/estimated/reported/unknown）、`PeerMetricsSnapshot`（asset_id + observed_tps/tvl/gas_fee + activity_breakdown + window_start/end/observed_at）、`snapshots_comparable()`（視窗不同/缺值/unit 不同/method 不同/source 不同→不可比較並附理由）。**無 API route、無 fixture、無 repository。** |
| `src/trustforge/ecolink.py` | 245 | 純資料契約：`DependencyEdge`（source/target/kind/valid_from/valid_until/confidence/official_source_url，僅允許 5 個官方 host）、`UpgradeEvent`（event_id/asset_id/status/impact_direction/impacted_asset_ids/official_source_url）。**無 impact-path 組裝函式、無 API route、無 fixture。** |
| `src/trustforge/ecolink_connector.py` | 87 | `parse_upgrade_events_fixture()`：**已經是 fixture parser**（吃 list[dict] payload，驗 host allowlist、驗 stale scheduled_at），可直接複用，不必重寫。 |
| `src/trustforge/tvl_connector.py` | 120 | PR #618（`fix/581-tvl-safe-fetch-followup`，已合併 develop）成果：SSRF-safe `fetch_tvl_metric()`，走 `safe_fetch.fetch_url`，回傳 `MetricValue(method=OBSERVED)`。**這是真實抓取路徑，屬於「另案」**，本輪不接線它；Peer API 一律吃 fixture，不呼叫 `fetch_tvl_metric`，避免與 #618 職責重疊或誤觸真實網路請求。 |
| `git grep peer_metric\|impact_path\|ecolink` in `web.py` | — | 零命中，確認完全沒接線（與 PLAN-context-next-increment-2026-07-24.md 優先2 的盤點一致）。 |
| `tests/test_peer_metrics_contract.py`／`test_ecolink_contract.py`／`test_ecolink_connector.py` | 416 行 | 只驗證 dataclass 契約本身，非 API 回歸測試。 |

**原始 issue 對照**：#577/#582/#587/#589（peer metrics 契約→API→比較邏輯的拆分序列）、#580/#586/#590（ecolink 契約→connector→impact path 的拆分序列）。#589/#590 目前只完成到「契約」那一棒，本計劃要接完剩下的 API + fixture + UI 棒次。

**可複用範本**：`feat/asset-context-lookup` 分支（模組①，PR #648 審查中）已提供完整端到端模式：
- 後端：`_handle_api_asset_context(qs)` handler + `Handler.do_GET` 內 `if u.path == "/api/asset-context":` 一行掛載、`_json_envelope_ok/_json_envelope_err` 信封、查無資料回 `200 {"asset_context": null}`（非 404，語意是「查無」非「請求錯」）。
- Repository：`AssetContextRepository`（`by_symbol(symbol, as_of)`，as-of 生效期查找）+ `load_asset_context_records(path)` 讀 `data/*.json` fixture。
- OpenAPI：`docs/api/openapi.yaml` 新增 path block（`OkEnvelope`/`ErrEnvelope` allOf 組合）。
- 前端：`lib/endpoints.ts` 的 `apiFetch<T>(path, params, validator, opts)` 呼叫模式、`lib/validators.ts` 的 `isXxxResponseData` 型別守衛、`lib/types.ts` 型別、獨立頁 `AssetContextLookupPage.tsx`（含空狀態/loading/error）、卡片元件 `SectorLayerCard.tsx`。
- 測試：`tests/test_asset_context_api.py`（API 層）+ `*.test.tsx`（前端元件）。

本計劃的每個 PR 都直接照這個模式複製，不重新設計架構。

**前端落點確認**：
- `ComparePage.tsx`（247 行）：雙幣比較頁，`data.report_a.coin` / `data.report_b.coin` 可拿到兩個 symbol；現有版面是 `grid grid-cols-1 lg:grid-cols-2` 兩欄報告卡。Peer 比較表適合加在這兩欄下方、`{data && (...)}` 區塊內新增一個 section（橫跨兩欄），用兩個 coin 打 Peer API。
- EcoLink 面板：獨立頁 `/eco-link?symbol=` 比照 `AssetContextLookupPage.tsx` 較合理（不綁死在雙幣比較情境，單一資產也該能查升級事件與影響路徑），並在 `Header.tsx` 加入口（比照模組①）。

---

## 1. 誠實鐵則（落地成具體規則，非口號）

1. **observed vs 理論峰值不混用**：`MetricValue.method` 只允許 `observed`（fixture 標記為「示範觀測值」）；fixture 資料**不放** TPS 理論峰值欄位，UI 不得出現「最高可達 X TPS」這類字樣。
2. **缺值/stale 擋比較**：沿用既有 `snapshots_comparable()`——任一方 `value is None` 或 `unit`/`method`/`source` 不同即回傳 `(False, reason)`；API 層把 `reason` 原樣透出，前端**必須**顯示「無法比較：{reason}」而非留白或補 0。
3. **時間偏差過大要擋**：peer snapshot 需同一 `window_start`/`window_end`（fixture 設計上直接讓所有同組資產共用同一比較窗，天然滿足；若未來接真實資料，`snapshots_comparable()` 已有視窗不同判斷）。
4. **跨協議 TVL 不包裝成精確事實**：所有 fixture TVL/TPS 數值在 API 回應與 UI 上必須伴隨 `"illustrative": true` 標記（見 §2 schema）+ UI 顯著文案「示範資料，非即時真實數值」，仿照模組①「查無資料顯示 unknown」的誠實原則，這裡是「有資料但要標示示範」。
5. **EcoLink 影響路徑不宣稱因果**：`ImpactDirection` 只在有 `DependencyEdge` + `UpgradeEvent` 官方來源 URL 時才組出路徑；confidence 過低（fixture 定義 threshold，如 `< 0.4`）或找不到路徑時，API 回 `"impact_paths": []` + `"verdict": "insufficient_data"`，前端顯示「資料不足，無法判定影響路徑」，不得自行外推因果語句。

---

## 2. 後端（優先做，UI 依賴其 schema）

### PR-1：Peer Metrics fixture 資料 + repository（無 API，先把資料層釘死）
- **改哪些檔**：
  - 新增 `data/peer_metrics_snapshots.json`：3 組同層資產（比照 sector 概念，示範用，非真實 COIN_POOL 五幣強制對齊——需求是「同層」不是「五幣」，故新增 `asset:arb`/`asset:op`/`asset:matic`（L2 rollup 組，呼應 `asset_context_records.json` 已有 ARB 範例）與 `asset:eth`/`asset:sol`/`asset:bnb`（L1 組，對齊既有 COIN_POOL），每組共用同一 `window_start`/`window_end`。每筆 `observed_tps`/`tvl`/`gas_fee` 皆 `method="observed"`、`source="fixture://peer-metrics/..."`，並在頂層加 `"illustrative": true`（fixture 專屬欄位，不進 dataclass frozen 契約，於 repository 組裝回應時外掛，避免動到既有 `PeerMetricsSnapshot` 契約簽章造成破壞性變更）。
  - 新增 `src/trustforge/peer_metrics_repository.py`：比照 `asset_context_repository.py`——`PeerMetricsRepository`，`by_asset_id(asset_id) -> PeerMetricsSnapshot | None`、`peer_group(asset_id) -> tuple[str, ...]`（回傳同組其他 asset_id，資料來自 fixture 內顯式 `"peer_group"` 欄位，不用演算法猜測，避免分組邏輯本身變成另一個誠實風險點）、`load_peer_metrics_fixture(path)`。
  - 新增 `tests/test_peer_metrics_repository.py`：讀 fixture、`by_asset_id` 命中/未命中、`peer_group` 回傳正確同組成員、`illustrative` 標記存在。
- **驗收**：`pytest tests/test_peer_metrics_repository.py tests/test_peer_metrics_contract.py -q` 全過；fixture JSON 通過既有 `PeerMetricsSnapshot`/`MetricValue` 契約驗證（repository 載入時用 `PeerMetricsSnapshot(...)` 建構即自動驗證，建構失敗代表 fixture 本身有錯）。
- **工時**：4h
- **reviewer**：CDO / db-architect（資料契約與 fixture schema 設計）
- **相依**：無，可立即開工
- **可平行**：可與 PR-3（EcoLink fixture）平行

### PR-2：`GET /api/peer-metrics?asset_id=` + OpenAPI + data-contracts artifact
- **改哪些檔**：
  - `src/trustforge/web.py`：新增 `_handle_api_peer_metrics(qs)`（照抄 `_handle_api_asset_context` 骨架）——參數 `asset_id`（必填），流程：查 repository → 查無回 `200 {"snapshot": null, "peers": []}` → 查有則對每個 peer 呼叫 `snapshots_comparable(target, peer)`，回應結構：
    ```json
    {
      "snapshot": {...PeerMetricsSnapshot.to_dict(), "illustrative": true},
      "peers": [
        {"asset_id": "...", "snapshot": {...}, "comparable": true, "reason": null},
        {"asset_id": "...", "snapshot": {...}, "comparable": false, "reason": "observed_tps missing"}
      ]
    }
    ```
    不設限流、不需認證（比照 `/api/asset-context`，唯讀觀測端點）。`Handler.do_GET` 內掛一行 `if u.path == "/api/peer-metrics":`。
  - `docs/api/openapi.yaml`：新增 `/api/peer-metrics` path block（`OkEnvelope`/`ErrEnvelope` allOf，`illustrative` 標記為 required boolean）。
  - **`python scripts/check_data_contracts.py --write`**：schema 變動後必跑，重生 artifact，連同 diff 一併提交（gray 曾踩過的坑，這裡明確提醒）。
  - `tests/test_peer_metrics_api.py`：命中/未命中/單一 peer 缺值不可比較（附 reason）/`illustrative` 欄位存在/400（缺 `asset_id`）/OpenAPI schema 與實際回應一致（比照 `test_asset_context_api.py` 寫法）。
- **驗收**：`pytest tests/test_peer_metrics_api.py -q`；`python scripts/check_data_contracts.py --check` 過（無 diff）；手動 `curl localhost:PORT/api/peer-metrics?asset_id=asset:arb` 回應含 `illustrative: true` 與至少一筆 `comparable: false`（示範缺值情境，故意讓其中一個 peer 缺一項指標，驗證誠實路徑真的會觸發，而不是理論上寫了但demo 資料剛好每項都齊）。
- **工時**：6h
- **reviewer**：CDO/db-architect（schema）+ harper（若牽涉前端契約溝通）
- **相依**：PR-1 完成
- **可平行**：與 PR-4（EcoLink API）平行

### PR-3：EcoLink fixture 資料（DependencyEdge + UpgradeEvent + Impact Path 組裝）
- **改哪些檔**：
  - 新增 `data/ecolink_dependency_edges.json` + `data/ecolink_upgrade_events.json`：示範 2-3 條依賴邊（如 `asset:arb → asset:eth` kind=settlement、`asset:op → asset:eth` kind=settlement）+ 2-3 個升級事件（`official_source_url` 必須落在既有 `OFFICIAL_ECOLINK_HOSTS` allowlist 內，如 `https://blog.arbitrum.io/...`，符合既有 host 驗證，不新增白名單，維持「只信任官方來源」的既有設計）。**其中至少一筆事件故意設計成 confidence 過低或缺 impacted_asset_ids，用來驗證「insufficient_data」誠實路徑會被觸發，而非只展示 happy path**。
  - 新增 `src/trustforge/ecolink_repository.py`：`EcoLinkRepository`，`dependencies_for(asset_id)`、`upgrade_events_for(asset_id)`、`load_ecolink_fixtures(edges_path, events_path)`（複用 `ecolink_connector.parse_upgrade_events_fixture` 讀事件，新增對應的 `parse_dependency_edges_fixture`，同樣走 host allowlist 驗證）。
  - 新增 `impact_paths_for(asset_id, *, min_confidence=0.4) -> tuple[ImpactPath, ...]`（新函式，放 `ecolink_repository.py`）：組裝 `UpgradeEvent → DependencyEdge → 受影響資產` 路徑，`confidence < min_confidence` 或找不到邊者不納入路徑，回傳空 tuple 由 API 層轉成 `verdict: insufficient_data`（§1 規則4 落地處）。
  - `tests/test_ecolink_repository.py`：路徑組裝正確性、confidence 過低被過濾、host 不在 allowlist 時載入報錯。
- **驗收**：`pytest tests/test_ecolink_repository.py tests/test_ecolink_contract.py tests/test_ecolink_connector.py -q` 全過。
- **工時**：5h
- **reviewer**：CDO/db-architect + harper（若要一併過一次 confidence 語意，可加 CDO 即可，不必都拉）
- **相依**：無（可與 PR-1 平行）

### PR-4：`GET /api/eco-link?asset_id=` + OpenAPI + data-contracts artifact
- **改哪些檔**：
  - `src/trustforge/web.py`：`_handle_api_eco_link(qs)`，回應：
    ```json
    {
      "dependencies": [DependencyEdge.to_dict(), ...],
      "upgrade_events": [UpgradeEvent.to_dict(), ...],
      "impact_paths": [
        {"event_id": "...", "path": ["asset:arb", "asset:eth"], "direction": "negative", "confidence": 0.7, "official_source_url": "..."}
      ],
      "verdict": "ok" | "insufficient_data"
    }
    ```
    不設限流、不需認證，`Handler.do_GET` 掛一行。查無資料回 200（同模組①「查無非請求錯」慣例）。
  - `docs/api/openapi.yaml`：新增 `/api/eco-link` path block。
  - `python scripts/check_data_contracts.py --write` 重生 artifact。
  - `tests/test_eco_link_api.py`：命中/未命中/`insufficient_data` 情境（用 PR-3 故意埋的低 confidence 事件驗證）/400/OpenAPI 一致性。
- **驗收**：`pytest tests/test_eco_link_api.py -q`；`python scripts/check_data_contracts.py --check` 無 diff。
- **工時**：5h
- **reviewer**：CDO/db-architect
- **相依**：PR-3 完成
- **可平行**：與 PR-2 平行

---

## 3. 前端（依賴 PR-2/PR-4 schema 定案后可先用 mock 開工，最後接真 API）

### PR-5：Peer 比較表（掛在 `ComparePage.tsx`）
- **改哪些檔**：
  - `frontend/src/lib/endpoints.ts`：`getPeerMetrics(assetId, signal)`，比照 `getAssetContext` 呼叫模式。
  - `frontend/src/lib/types.ts` + `validators.ts`：`PeerMetricsResponseData`、`isPeerMetricsResponseData`。
  - 新增 `frontend/src/components/PeerComparisonTable.tsx`：desktop 用 `<table>`（欄：資產／observed TPS／TVL／Gas／活躍度 breakdown），mobile（<640px）改用堆疊 card（比照現有 `SectorLayerCard` 的 grid→card 響應式模式）；每列若 `comparable: false` 顯示「無法比較：{reason}」灰階列而非硬湊數字；表頭固定顯示「⚠ 示範資料（illustrative），非即時真實數值」banner。
  - `ComparePage.tsx`：在 `{data && (...)}` 兩欄報告下方新增一個橫跨兩欄的 section，用 `data.report_a.coin`/`data.report_b.coin` 各打一次 `getPeerMetrics`，並排顯示兩個資產各自的 peer 表（或合併成一張以兩者為軸心的比較表，實作時依 API 回應形狀決定，若 UI 複雜度暴增則保留各自獨立表這個較簡單版本）。
  - `frontend/src/components/PeerComparisonTable.test.tsx`：資料齊全 happy path、含 `comparable:false` 列顯示 reason、mobile breakpoint 快照。
- **驗收**：desktop + 375px 皆測；illustrative banner 一定出現；缺值列顯示具體 reason 文字（非空白/非 0）；`npx vitest run` 過。
- **工時**：6h
- **reviewer**：CTO 線（前端整合）+ harper（UI 一致性/mobile）
- **相依**：PR-2 API schema 定案（可先用 mock response 開工，最後一天接真 API 換 mock 為真呼叫）
- **可平行**：與 PR-6 平行

### PR-6：EcoLink 影響路徑面板（獨立頁 `/eco-link?symbol=`）
- **改哪些檔**：
  - `frontend/src/lib/endpoints.ts` + `types.ts` + `validators.ts`：`getEcoLink`、`EcoLinkResponseData`、`isEcoLinkResponseData`。
  - 新增 `frontend/src/pages/EcoLinkPage.tsx`：比照 `AssetContextLookupPage.tsx` 骨架（symbol 查詢框、loading/error/空狀態）。
  - 新增 `frontend/src/components/ImpactPathPanel.tsx`：列出 `upgrade_events` 時間軸 + 每個事件對應的 `impact_paths`（事件→依賴邊→受影響資產，附 `official_source_url` 連結、confidence 數值、`direction` 標籤）；`verdict === "insufficient_data"` 時整面板顯示「資料不足，無法判定影響路徑」置中文案，不渲染任何路徑列表（避免半吊子資料誤導）；每條路徑旁不加「因此」「將導致」等因果字樣，改用「可能相關」中性措辭（誠實鐵則落地在文案層級）。
  - `App.tsx` 路由 + `Header.tsx` 入口（比照模組① `/asset-context` 掛法）。
  - `frontend/src/components/ImpactPathPanel.test.tsx` + `EcoLinkPage.test.tsx`：happy path、`insufficient_data` 情境、官方來源連結可點擊且指向 allowlist host。
- **驗收**：desktop + 375px；`insufficient_data` 情境必須有測試覆蓋（不能只測 happy path）；連結需為真實可點的 `<a href>` 而非純文字。
- **工時**：6h
- **reviewer**：CTO 線 + harper；建議加 **harper 一起檢視「不宣稱因果」文案措辭**（反作弊/誠實面向，若團隊有獨立 harper adversarial review 角色可再加審一輪，見 `docs/plans` 既有「Luna adversarial PR review」慣例，此處沿用同精神但由 harper 承接前端文案審查）。
- **相依**：PR-4 API schema 定案（同 PR-5，可先 mock）
- **可平行**：與 PR-5 平行

---

## 4. 序列與工時彙總

```
PR-1 (4h, 後端fixture) ─┬─→ PR-2 (6h, Peer API) ─┬─→ PR-5 (6h, Peer UI)
PR-3 (5h, 後端fixture) ─┴─→ PR-4 (5h, EcoLink API)┴─→ PR-6 (6h, EcoLink UI)
```
- PR-1、PR-3 可完全平行開工（互不相依）。
- PR-2 依賴 PR-1；PR-4 依賴 PR-3；PR-2/PR-4 互相平行。
- PR-5/PR-6 理論上可等 PR-2/PR-4 API schema 定案後用 mock 先行開工（不需等後端 PR merge），最後一天接真 API 收尾；若要保守排法，PR-5 依賴 PR-2 merge、PR-6 依賴 PR-4 merge。

**總工時**：4+6+5+5+6+6 = **32h**（略高於 CEO 提及的 20-28h 區間，原因：本計劃額外把「故意埋一筆缺值/低 confidence 情境並補測試」「data-contracts artifact 重生」「mobile 響應式表格」三項算進工時，這些是先前 PLAN-context-next-increment 估的 20-28h 只涵蓋「後端 fixture-based API」未含前端；若要收斂到 28h 內，可考慮 PR-5/PR-6 各砍 1-2h（例如兩表格共用同一 `PeerComparisonTable` 元件、不分別測 desktop/mobile 快照兩套），但不建議砍「insufficient_data / comparable:false 測試」——這是誠實鐵則的驗收核心，砍了等於自打嘴巴。若 CEO 要求嚴守 28h，建議明確裁示砍哪個小項而非籠統要求。

**Reviewer 總表**：

| PR | 內容 | 工時 | reviewer | 相依 | 可平行 |
|----|------|------|----------|------|--------|
| PR-1 | Peer fixture + repository | 4h | CDO/db-architect | 無 | PR-3 |
| PR-2 | `/api/peer-metrics` + OpenAPI + contracts | 6h | CDO/db-architect | PR-1 | PR-4 |
| PR-3 | EcoLink fixture + repository + impact path 組裝 | 5h | CDO/db-architect | 無 | PR-1 |
| PR-4 | `/api/eco-link` + OpenAPI + contracts | 5h | CDO/db-architect | PR-3 | PR-2 |
| PR-5 | Peer 比較表 UI（掛 ComparePage） | 6h | CTO 線 + harper | PR-2（schema 定案後可先 mock） | PR-6 |
| PR-6 | EcoLink 影響路徑面板 UI（獨立頁） | 6h | CTO 線 + harper | PR-4（schema 定案後可先 mock） | PR-5 |

---

## 5. 與 PR #618（TVL safe fetch）的協調

- PR #618 已合併 develop（`fix/581-tvl-safe-fetch-followup`），成果是 `tvl_connector.py` 的 SSRF-safe 真實抓取路徑（`fetch_tvl_metric()` 打 `api.llama.fi`/`defillama.com`）。
- 本計劃的 PR-1/PR-2 **不呼叫** `fetch_tvl_metric`，Peer fixture 的 TVL 欄位全部走 `method="observed"` + `source="fixture://..."`，不打真實網路，避免：
  1. 與 #618 的 SSRF-safe 邊界職責重疊（真實抓取邏輯應該只有一份，不要在 peer_metrics 這邊重新發明）。
  2. Demo 環境對外網路依賴（fixture 保證離線可跑、可重複執行）。
- 若未來要把 Peer TVL 從 fixture 換成真實抓取，屬於「另案」（如 CEO 定調），届時應直接複用 `tvl_connector.fetch_tvl_metric()`，而非另開一條抓取路徑，避免兩套 TVL 抓取邏輯並存造成 host allowlist/超時設定不一致的風險。

---

## 6. 需 CEO 裁示點

1. **總工時 32h vs 目標 20-28h**：是否接受多出的 4-6h（用於誠實鐵則測試覆蓋 + mobile 響應式 + data-contracts artifact），或指定砍哪個小項對齊 28h 上限（不建議砍誠實鐵則測試）。
2. **Peer 分組資料範圍**：本計劃新增 L2 組（ARB/OP/MATIC，呼應模組①已有 ARB fixture）與既有 COIN_POOL L1 組（ETH/SOL/BNB），需要 CEO/PM 確認這是否是 demo 想呈現的「同層」分組，或要換成別的示範組合。
3. **PR-5/PR-6 是否等後端 merge 才開工**：若要壓縮總時程，前端可用 mock 先行（開發時間重疊，但有「mock 與最終 API schema 對不上要返工」的風險）；若求穩，則走嚴格序列相依。
4. **EcoLink UI 落點**：本計劃選擇獨立頁 `/eco-link?symbol=`（比照模組①模式）而非塞進 `ComparePage`，需確認 demo 敘事是否需要這個獨立入口，或希望整合進雙幣比較頁（會增加 PR-6 複雜度與工時）。
