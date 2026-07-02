# PLAN — Axis C #1：多幣信任分快照寫入者 + 首頁總覽（正確讀路徑）

> 對應 master `WORLD-FIRST-MASTER-PLAN.md` §4.1（Glassnode Point-in-Time 差異化）task #23。
> 撰寫：gray（CPO）。不改 code，先看現況再開方案。

## 現況 spot-check 結論

- `scripts/fetch_scheduler.py`：唯一打真連接器 API 的地方，cron 每 10 分鐘跑
  一次「全部來源」，內建新鮮度守門 + `--dry-run`/`--probe`。收尾已有
  `append_scheduler_run()` 寫執行紀錄的先例，可比照加「快照寫入」收尾步驟。
- `ingestion/cache.py`：`cache_key(name, coin)` + `cache_get/cache_set` 已是
  通用 KV 介面（`backend.set(key, docs, fetched_at, ttl_seconds)`），**不限
  Document**——`run_probe()` 就是塞一個假 sentinel 進去的先例。可直接沿用
  同一張表（`trustforge-connector-cache`），**不需新表、不需新 IAM**。
  `DynamoDBCache.__init__` 已預留 `connect_timeout/read_timeout/max_attempts`
  三參數（docstring 明寫：「留著給 Axis C 用」）。
- `web.py::_render_home_page()`：docstring 已明載 P3 事故——曾在首頁 request
  對 5 幣各讀 DynamoDB，codex 抓出 ThreadPool 孤兒執行緒可用性 HIGH，已整個
  移除，等 Axis C 做對再加回。`_render_status_page_cached()` /
  `_get_ledger_summary()` 已是「module 級 TTL 快取 + 鎖內 single-flight」的
  驗證過模式，可直接套用在總覽區塊。
- `pipeline.run(data_mode="live", llm_mode="off")`：`collect()` 線上分支已是
  `CachedSource`-only（cache-miss 就 raise，優雅降級進 `report.limits`），
  **不打真連接器、不打 Bedrock**，純 CPU 確定性運算，$0 confirmed。

## 方案

### 1. 快照寫入者
- 位置：`fetch_scheduler.py` 新增 `--snapshot` 模式（獨立 cron line，**不**
  綁在既有「打真 API」流程內，解耦 refresh 節奏）。
- 動作：對 5 幣各呼叫一次 `pipeline.run(coin, generic_query, MULTI_SOURCE,
  data_mode="live", llm_mode="off")`，讀既有 cache，純運算。
- 精華欄位：`{coin, trust_score, direction, calibrated_confidence,
  decision_state, generated_at}`（來自 `Report` 既有欄位，不新造）。
- 寫入：沿用 `cache_set(backend, cache_key("__trust_snapshot__", coin),
  [snapshot_dict], fetched_at, ttl_seconds)`——`docs` 塞單一 dict，比照
  `run_probe()` 的既有寫法，複用同一張表/同一套 fallback 語意。
- Cadence：建議 15 分鐘一次（`SNAPSHOT_REFRESH_INTERVAL_SECONDS`，比照
  `stale_after_for()` 3 倍 margin → 45 分過期），失敗單幣不中斷整批。
- ⛔ #24：只寫 `pipeline.run()` 真跑出來的結果；任一幣 collect 全失敗/
  `docs` 為空則該幣本輪跳過不寫（不得補假值）。

### 2. 首頁總覽正確讀路徑
- 寫入者 5 幣快照算完後，**順便**組一份 5 卡片總覽 HTML，寫入單一 key
  `cache_key("__trust_overview_html__", "")`（同一張表，同一 TTL 邏輯）。
- 首頁 request：套用 `_render_status_page_cached()` 同款模式——module 級
  TTL 快取（建議 60 秒）+ 鎖內 single-flight；TTL miss 時**只發生一次**
  `cache_get()`，讀那顆預渲染 blob，使用短 timeout 版 `DynamoDBCache`
  （`connect_timeout≈1s, read_timeout≈1s, max_attempts=1`）。
- 讀失敗/miss/timeout：不顯示總覽區塊（首頁其餘內容照常渲染），且**把
  「失敗」結果也放進同一份 TTL 快取**（短 TTL，如 15 秒），避免斷網期間
  每個 request 都重新等一次 timeout——這是比 P3 更進一步的加固。
- 鐵則覆核：全程零 ThreadPool、零逐幣讀取、單次讀取有界 timeout、
  TTL+single-flight 避免 thundering herd。

### 3. 歷史面（roadmap）
- 本輪快照 key 為「latest 覆蓋」，不建立時序累積——多日趨勢視圖需要 #20
  結果持久化到位後再做（`trust/history_store.py`，見 master §4.1），此輪
  僅列 roadmap 標註，不展開實作。

## 改動範圍（供 CTO 執行）
- `scripts/fetch_scheduler.py`：新增 `--snapshot` 分支 + CLI flag + cron doc
- `ingestion/cache.py`：新增 `SNAPSHOT_REFRESH_INTERVAL_SECONDS` 常數（可選，
  或沿用 fallback 間隔）
- `src/trustforge/web.py`：`_render_home_page()` 加回總覽區塊 + 新
  `_render_home_overview_cached()`（比照 `_render_status_page_cached`）
- `deploy/README.md`：新增 `--snapshot` cron line 說明

## CEO 驗收
1. Chrome 開首頁：5 幣各顯示真信任分卡（分數/方向/校準信心），非空白/非假資料
2. Network tab / 加 log 確認首頁 render 全程 0 次逐幣 DynamoDB 呼叫，僅 1 次
   （或 TTL 命中時 0 次）blob 讀取
3. 模擬斷網/慢 backend（拔 AWS 憑證或改極長 delay）：首頁仍秒開，僅總覽區
   優雅缺席，其餘頁面正常
4. `--probe`/existing tests 綠燈，新增 `--snapshot --dry-run` 驗證不誤打真 API

## 風險
- 15 分鐘 cadence 若跟 5 幣 collect 同時全部 cache-miss（冷啟動）→ 快照為空，
  首頁總覽該次不顯示，非 bug，等下一輪
- 短 timeout 若設太緊可能誤判健康 backend 為逾時——需與 CTO 實測校準數值
