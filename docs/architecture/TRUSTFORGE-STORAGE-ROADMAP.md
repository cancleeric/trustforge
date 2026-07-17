# Storage Roadmap：SQLite → Parquet/DuckDB → Iceberg

## 現階段：SQLite operational truth

SQLite 保留本機與單節點 daemon 的 transactional control plane：活動題目、分析 queue/checkpoint、不可變 snapshot、結果、lineage、feature values、upgrade approvals。來源 Bronze 與 quarantine 目前也共置 SQLite，便於零網路開發與稽核。

禁止再把 `connector_cache` 當歷史資料。歷史入口只有 `source_events`；分析歷史入口只有 snapshot/result/lineage；訓練輸入只有 point-in-time Feature Store。

## 下一階段：Parquet + DuckDB local analytics

當 `source_events` 單庫超過 10 GB、日查詢超過 10 萬列或 replay 全掃 p95 超過 30 秒時，將 immutable Bronze、quarantine、lineage、feature values 依日期／source／coin 匯出 Parquet。SQLite 仍保留 queue 與 active pointers；DuckDB 只讀 Parquet 做 coverage、replay、品質與訓練資料集查詢。

匯出必須有 row count、min/max time、schema version、SHA-256 manifest，且完成逐列／聚合核對後才可刪除 SQLite 冷資料。切換期 dual-write 禁止；採 append SQLite → sealed partition export，避免兩套寫入真相。

## Production scale：S3 + Iceberg

當多 worker／多節點需要共享歷史或日增量超過本機磁碟安全範圍時，Bronze/Silver/Gold 進 S3 Iceberg。partition 以 event date/source/coin 設計，保留 schema evolution、snapshot isolation、time travel、retention 與 compaction。Trust Kernel、feature contract、human release gate 不因儲存層切換而改變。

進入 Iceberg 前必須完成：KMS、bucket policy、object lock/retention、catalog 權限、跨帳號邊界、成本預估、restore drill，以及 SQLite/Parquet/Iceberg 三方 checksum reconciliation。未達門檻時維持 SQLite，不為架構展示提前增加營運風險。
