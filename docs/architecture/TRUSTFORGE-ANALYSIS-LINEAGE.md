# Hermes 分析 Lineage

Hermes 的每份結果都能由 `analysis_lineage_events` 反向追溯到固定 snapshot 與完整五階段執行：

`snapshot_created → job_enqueued → stage_started/stage_completed → result_published`

每個事件保存 `snapshot_id`、`job_id`、stage、entity／parent 關係、時間與非敏感執行 metadata。SQLite trigger 禁止 UPDATE／DELETE；重試與失敗追加新 event，不覆蓋先前歷史。`AnalysisFlow.lineage(job_id=...)` 或 `lineage(snapshot_id=...)` 提供稽核查詢。

Lineage 實作同時暴露並修正一個重複執行問題：同一個 process 在 `enqueue_matrix()` 後呼叫 `start()`，舊版 `recover()` 會再次收養記憶體中已經排隊的 job，造成每一階段跑兩次。現在 recovery 只接手尚未被本 process 收養的 durable job；daemon 重啟仍會從 immutable snapshot 正常恢復，而新建立 job 不再重複分析。
