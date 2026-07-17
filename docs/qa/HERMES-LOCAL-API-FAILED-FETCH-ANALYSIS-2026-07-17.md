# Hermes 本機 API `Failed to fetch` 分析報告與執行計畫

日期：2026-07-17
狀態：根因已定位；transport、SQLite 連線風暴與 journey N+1 已修，待一般 Chrome 視覺驗收

## 1. 使用者可見症狀

- `http://127.0.0.1:4174/?workspace=history&coin=BTC` 可載入 React 外框。
- 歷史工作區顯示 `連線異常 network_error / Failed to fetch`。
- 左側服務監視器將 overview、health、costs 顯示為 DOWN；history 偶爾顯示 UP，造成互相矛盾的狀態。
- SQLite 內已有歷史與分析結果，問題不是「沒有資料」。

## 2. 已確認的事實

### 2.1 冷啟動的單支 API 曾可用，但不代表並發健康

- Vite：`127.0.0.1:4174` 正常 LISTEN。
- Python API：`127.0.0.1:8799` 正常 LISTEN。
- 經 Vite 同源 proxy 實測：
  - `/api/health`：HTTP 200，約 0.10 秒。
  - `/api/history?coin=BTC&days=30`：HTTP 200，約 0.04 秒，回傳 16 KB 歷史資料。
  - `/api/status`：HTTP 200，約 0.12 秒。
  - `/api/costs`：HTTP 200，約 0.11 秒。
  - `/api/analysis-flow`：HTTP 200，約 1.04 秒。

這組早期單支測量只證明資料存在，後續並發驗證推翻了「API 整體健康」的
判斷：儀表板同時請求七支端點時，多支請求超過 15 秒。

### 2.2 內建瀏覽器結果排除，不作產品根因證據

Codex 內建瀏覽器曾額外發出 production domain fallback 並回報 CORS；該 URL
不存在於 frontend source，且一般 Chrome 無相同行為。依使用者指示，後續
停止使用內建瀏覽器；該現象只記為工具環境異常，不列入 TrustForge 根因。

API 仍加入嚴格本機 allowlist CORS 作 transport hardening，但它不是本次慢載入
與閃爍的唯一修復。

### 2.3 一般 Chrome 與行程取樣的直接證據

- 一般 Chrome 將錯誤穩定重現為「請求超過 10 秒無回應」，不是 production
  domain CORS。
- 七支啟動 API 並發時，health 約 6.8 秒，其餘多支超過 15 秒。
- 卡住行程累積 500 多條 `process_request_thread`，實體記憶體約 5.0 GB。
- macOS `sample` 顯示請求執行緒大量停在 SQLite connection close、statement
  finalize、JSON decode 與 GIL/allocator contention。
- Web 每個讀取請求重建 SQLite backend；`AnalysisFlow()` 每次輪詢又重跑
  schema/WAL 初始化；journey `limit=100` 使用 2N+2 查詢（202 queries）。
- 前端逾時後持續輪詢，形成「慢讀 → abort/BrokenPipe → 新請求 → 更多 thread」
  的正回饋雪崩。

### 2.4 Hermes 分析流水線的獨立問題

- daemon 已由單 worker 改為每階段 4 workers，歷史積壓持續下降。
- 修復前排程容量到 500 時丟出 `RuntimeError`，導致每輪 refresh 以錯誤結束。
- 本分支已先完成但尚未交付的修正：容量滿改為正常 backpressure、同一 immutable snapshot 可在後續 refresh 補齊未入列矩陣、worker watchdog 可從 SQLite snapshot 恢復遺失的 in-memory package。
- 這個流水線問題會造成結果更新慢，但不是本頁 `Failed to fetch` 的直接原因；兩者必須分開驗收。

## 3. 根因判定

主因是無界 `ThreadingHTTPServer` 加上每請求 SQLite 連線/schema 初始化與
journey N+1 查詢；前端多通道輪詢將延遲放大成 thread/memory 雪崩，最後同時
出現 reset、timeout、BrokenPipe 與短暫拒絕連線。CORS 是需要補強的 transport
邊界，但一般 Chrome 的真實故障由上述資源雪崩造成。

## 3.1 已實作修正與量測

- Web 本機 SQLite backend 改為 process 級共用、保留既有 RLock 保護。
- active request thread 上限 32；超載時快速回 HTTP 503 + `Retry-After: 2`，
  不再無界吃記憶體。
- AnalysisFlow 新增真正 read-only SQLite projection：不 mkdir、不建 schema、
  不切 WAL，唯讀 API 使用 `mode=ro`。
- journey 改為 4 個 bounded queries，不再對每個 job 各查 stages/attempts。
- `_send()` 同時捕捉 header flush 與 body write 的 BrokenPipe。
- 修後七支啟動 API 並發全部成功：0.75–3.71 秒，沒有 10/15 秒 timeout。
- 15 支短壓（5× flow/journey/history）修正 N+1 後全部 HTTP 200，最慢約
  6.49 秒；修正前同測有 5 支超過 10 秒。
- 一般 Google Chrome（非 Codex 內建瀏覽器）驗收證據：
  [`BTC history`](./hermes-history-btc-chrome-2026-07-17.png)、
  [`ETH history`](./hermes-history-eth-chrome-2026-07-17.png)。兩張均無錯誤卡，
  URL 幣別、選幣元件、中央趨勢與右側焦點/分數一致切換（BTC 54、ETH 55）。

## 4. 修復原則

1. Production 繼續預設不開放任意跨域。
2. 僅在明確設定的本機 allowlist 下回應 CORS；不得使用 `*`。
3. 只允許 `http://127.0.0.1:4174` 與必要的 `http://localhost:4174`。
4. 回傳 `Vary: Origin`，避免不同 Origin 共用錯誤快取。
5. 支援必要的 OPTIONS preflight，但只允許既有 API 使用的方法與 headers。
6. launchd 明確注入 allowlist；未設定環境變數時維持原本的 same-origin 安全邊界。
7. 前端不以重試迴圈掩蓋錯誤；修復應落在 transport 邊界。

## 5. 執行計畫

### Phase A — 後端 transport 修復

- 新增嚴格解析的本機 CORS allowlist。
- 在 `_send()` 僅對允許的 Origin 加入 CORS headers。
- 新增 OPTIONS handler 與拒絕未知 Origin 的測試。
- 更新本機 launchd plist，production deployment 不受影響。

### Phase B — 排程可靠性完成

- 完成 queue backpressure 測試。
- 驗證 daemon 不再新增 `analysis queue capacity reached` traceback。
- 觀察 pending 數持續下降且五階段仍重疊執行。

### Phase C — 實機驗收

- 桌面尺寸重新載入 history：不得出現 `network_error`。
- 切換 BTC/ETH：URL、中央歷史圖、右側焦點與數字同步。
- 分析頁：底部顯示目前幣別、模式、題目、snapshot、各階段事件數與耗時。
- 手機尺寸：左右欄與訊息面板不得遮住主要內容。
- 保存桌面與手機 screenshot，並記錄 console/network 結果。

### Phase D — 交付

- 執行 targeted tests、完整 pre-push tests、frontend tests/build。
- 更新 Hermes delivery backlog 與未完成清單。
- commit、push、建立 PR；PR 連結對應未結 issue，不宣稱未驗證項目已完成。

## 6. 完成定義

- API 在 allowlisted 本機 Origin 可由瀏覽器讀取；未知 Origin 不取得 CORS 授權。
- history 不再顯示 `Failed to fetch`。
- overview、health、history、costs 的 HUD 狀態與實際 API 一致。
- Hermes queue capacity 是可觀測 backpressure，不是 daemon exception。
- 桌面與手機實機證據、測試結果、PR 均齊全後才可標記完成。
