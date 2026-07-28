# Tasks：Arkham + Whale Alert Live Integration

## Task 1：新增 helper 函式（`whale_trades.py`）

- [ ] 新增 `_ARKHAM_COIN_CHAINS` 字典（BTC→bitcoin, ETH→ethereum, SOL→solana, BNB→bsc, XRP→xrp）
- [ ] 新增 `_parse_iso_timestamp(ts_str)` → `float | None`
- [ ] 新增 `_extract_entity_name(addr_obj)` → `str`
- [ ] 新增 `_has_arkham_attribution(addr_obj)` → `bool`

**檔案**：`src/trustforge/ingestion/whale_trades.py`
**需求**：R2

---

## Task 2：重寫 `ArkhamIntelSource.fetch()`

- [ ] Base URL 改為 `https://api.arkm.com`
- [ ] 認證改為 `extra_headers={"API-Key": api_key}`（移除 query param `apiKey`）
- [ ] 參數改為 `usdGte`, `timeLast=1h`, `limit=20`, `chains=<mapped>`
- [ ] 移除 `params["base"]`，改用 `params["chains"]` + `_ARKHAM_COIN_CHAINS` 映射
- [ ] 確認 `safe_fetch` 的 `extra_headers` 正確傳遞 API-Key header

**檔案**：`src/trustforge/ingestion/whale_trades.py`
**需求**：R1

---

## Task 3：重寫 `ArkhamIntelSource._parse_transfer()`

- [ ] 幣種從 `transfer["tokenSymbol"]`（頂層字串）取得
- [ ] 金額從 `transfer["historicalUSD"]`（頂層浮點數）取得
- [ ] 時間戳用 `_parse_iso_timestamp(transfer["blockTimestamp"])` 解析
- [ ] 來源/目的實體用 `_extract_entity_name()` 萃取
- [ ] 驗證狀態用 `_has_arkham_attribution()` 判斷
- [ ] 買賣方向邏輯保持不變（有歸因的 toAddress → buy）
- [ ] Document 產出格式維持不變（id/kind/source/text/url/ts/meta）

**檔案**：`src/trustforge/ingestion/whale_trades.py`
**需求**：R2

---

## Task 4：更新 docstring 與白名單說明

- [ ] 更新 class docstring：端點改為 `https://api.arkm.com/transfers`
- [ ] 更新 class docstring：認證改為 `API-Key header`
- [ ] 更新模組頂部白名單說明：`api.arkhamintelligence.com` → `api.arkm.com`

**檔案**：`src/trustforge/ingestion/whale_trades.py`
**需求**：R1

---

## Task 5：更新測試 mock

- [ ] 找到所有 mock Arkham 回應的測試檔案
- [ ] Mock 回應結構更新為 v1.1.0 schema（`tokenSymbol`, `historicalUSD`, ISO timestamp, 巢狀 entity/label）
- [ ] 新增測試：`_parse_iso_timestamp` 各種格式（Z 尾綴、+00:00、無效字串）
- [ ] 新增測試：`_extract_entity_name` 優先順序（entity > label > address truncate）
- [ ] 新增測試：`_has_arkham_attribution` 正反例
- [ ] 確認既有 whale_trades 測試全數通過

**檔案**：`tests/test_whale_trades*.py`
**需求**：R2, R5

---

## Task 6：Whale Alert API key 端到端驗證

- [ ] 設定環境變數 `WHALE_ALERT_API_KEY=nmONwfLZ3rPYaMiKeC0zLBbudgseYEsi`
- [ ] 手動或腳本呼叫 `WhaleAlertSource().fetch("", coin="BTC")`
- [ ] 確認回應 `result=success`（或免費層限制訊息）
- [ ] 記錄驗證結果

**需求**：R3, R4

---

## Task 7：Arkham API key 端到端驗證

- [ ] 設定環境變數 `ARKHAM_API_KEY=<your_key>`
- [ ] 手動或腳本呼叫重寫後的 `ArkhamIntelSource().fetch("", coin="BTC")`
- [ ] 確認回應可正確 parse 為 Document（或 401/403 → 降級空 list）
- [ ] 記錄驗證結果與 API 計畫狀態

**需求**：R1, R4

---

## Task 8：回歸測試 + lint

- [ ] `python -m pytest tests/ -x -q`（全量測試通過）
- [ ] `python -m pytest tests/test_whale_trades*.py -v`（聚焦驗證）
- [ ] lint 通過（若有 ruff/flake8 配置）
- [ ] 確認離線模式 `collect(offline=True)` 仍正常產出 whale_onchain + celebrity_trade Document

**需求**：R5, R6

---

## 依賴關係

```
Task 1 → Task 2, Task 3（helper 先到位）
Task 2 + Task 3 → Task 4（改完再更新文件）
Task 2 + Task 3 → Task 5（改完再更新測試 mock）
Task 5 → Task 8（測試先更新再跑回歸）
Task 2 → Task 7（fetch 改完才能驗證）
Task 6 可平行（WhaleAlert 不動程式碼）
```

---

## 預估工時

| Task | 估計 | 備註 |
|------|------|------|
| T1 | 10 min | 純新增 helper |
| T2 | 10 min | fetch 重寫 |
| T3 | 15 min | parse 重寫 |
| T4 | 5 min | docstring |
| T5 | 20 min | 測試最費工 |
| T6 | 5 min | curl 或 script |
| T7 | 5 min | curl 或 script |
| T8 | 5 min | 跑測試 |
| **合計** | **~75 min** | |
