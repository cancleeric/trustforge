# Tasks

## Task 1：建立 connector 骨架
- 檔案：`src/trustforge/ingestion/whale_trades.py`
- 實作 `WhaleAlertSource(Source)` — kind=`whale_onchain`
- 實作 `ArkhamIntelSource(Source)` — kind=`celebrity_trade`
- 實作 `build_whale_sources() -> list[Source]` 工廠函式
- 安全措施：safe_fetch、timeout、size limit、URL 白名單

## Task 2：離線樣本資料
- 檔案：`demo/sample_data/whale_trades.json`
- 提供 8-10 筆涵蓋不同情境的樣本：
  - 交易所流出（看漲訊號）
  - 交易所流入（賣壓訊號）
  - 已驗證名人買入/賣出
  - 未驗證名人宣告（測試降級邏輯）

## Task 3：整合到 collect() 流程
- 在 `base.py` 的 `collect()` 中加入延遲匯入 + 呼叫
- 在 `SOURCE_KINDS` 中考慮是否新增（或保持獨立於文件型來源）

## Task 4：信譽與評分整合
- `scoring.py` 的 `KIND_REPUTATION` 新增兩個 kind
- 實作動態降級（未驗證 → 降為 social 信譽）
- 新增 `KIND_HALFLIFE_HOURS` 或在 `_recency_decay` 中特化處理

## Task 5：快取層支援
- 確認 `cache.py` 的 `CachedSource` 能正確包裝新 connector
- 在 `fetch_scheduler.py` 中加入排程設定

## Task 6：驗證與測試
- 確認 offline collect() 能正確載入 whale_trades.json
- 確認 scoring 能正確處理新 kind 的信譽/衰減
- 確認 import chain 正常運作
