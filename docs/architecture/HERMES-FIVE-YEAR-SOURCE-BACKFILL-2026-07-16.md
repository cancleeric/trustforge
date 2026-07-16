# Hermes 五年多來源每日回填

## 決策

Hermes 要以五年內每一天可合法取得的來源資料建立 point-in-time snapshot，
再送入既有每日 replay。這不是模型 fitting 專案；目前優先補齊來源封存、
時間邊界與可稽核 lineage。OHLCV、來源 Evidence、信任結果是三個不同資料層，
禁止互相冒充。

## 資料契約

每筆回填資料必須保存 `provider`、`source`、`kind`、`published_at`、實際
`retrieved_at`、URL、license/contract 與 content hash。`published_at` 決定
歷史日 T 是否可見；`retrieved_at` 只記錄今天何時回填，不能偽造為當年抓取
時間。所有回填 snapshot 標記 `backfilled_archive`。同一天可合併多個
provider；相同 content hash 去重。無正式歷史介面或授權的來源保持 missing。

## 來源能力矩陣

| 來源 | 歷史策略 | 狀態 |
|---|---|---|
| Alternative.me Fear & Greed | 官方完整歷史 API | ready |
| SEC EDGAR | 官方 quarterly master index | ready-partial（metadata-only，不宣稱全文命中） |
| CoinGecko market range | range API | credential/plan gated |
| 新聞 RSS | publisher archive 或授權資料集 | recent RSS 不可回填五年 |
| Reddit | 官方 archive 或授權資料集 | recent RSS 不可回填五年 |
| current-state on-chain | historical chart/block/dataset API | 需逐 provider adapter |
| HOYA ticker | 正式 endpoint 與資料契約 | blocked |

能力矩陣由 `trustforge.historical_sources` 投影到 Hermes Upgrade Control，讓管理者
直接看見 ready、gated、archive-required 與 blocked，不以空泛的「有來源」取代
真實覆蓋狀態。

## 執行流程

1. source adapter 只拉取指定日期範圍，輸出 provenance-complete JSONL。
2. `historical_backfill.py` 驗證並寫入來源 snapshot；同日多 provider 合併。
3. 每日 replay 只能讀取 `published_at <= T` 的內容。
4. 缺來源仍記錄 missing，不用今天的搜尋結果補造過去。
5. 等來源覆蓋與 outcome gate 達標後，才重新評估校準器或其他 fitting。

第一個 adapter：

```bash
python scripts/fetch_public_history.py \
  --source alternative-me-fng \
  --from-date 2021-06-01 --to-date 2026-05-31 \
  --out out/history/alternative-me-fng.jsonl
```

輸出可交給既有 `scripts/historical_backfill.py` 匯入。網路失敗、provider 格式錯誤
或空區間都必須顯式失敗或回報零筆，不得生成推測資料。

SEC quarterly index 需要符合 SEC automated-access policy 的識別 user agent：

```bash
python scripts/fetch_public_history.py \
  --source sec-gov --from-date 2021-06-01 --to-date 2026-05-31 \
  --user-agent "Organization contact@example.com" \
  --out out/history/sec-gov-metadata.jsonl
```

此 adapter 只依 company/form metadata 過濾，輸出會標記
`match_scope=metadata_only`。它補的是官方申報索引 lineage，不等同全文搜尋；
全文 Evidence 仍需後續受控下載 filing 或正式全文 archive adapter。
