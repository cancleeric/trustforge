# Append-only Source Events（Bronze）

`source_events` 是連接器每次成功 fetch 的不可變歷史真相；`connector_cache` 只是供產品快速讀取的 latest-value projection，不能再被當成歷史資料來源。

排程寫入順序固定為：

1. 連接器完成一次真實 fetch。
2. 將該次完整 Document batch、內容 hash、幣別、時間、來源、HTTP metadata、`fetch_run_id` 與 `scheduler_run_id` append 到 `source_events`。
3. Bronze append 成功後，才更新 `connector_cache`。
4. Bronze append 失敗時，本輪標記失敗且保留原 cache，不讓未封存資料冒充可稽核新資料。

SQLite 以 `BEFORE UPDATE`／`BEFORE DELETE` trigger 強制不可變。內容重複的兩次 fetch 仍保留為兩個 event，供新鮮度、來源穩定度與 replay 稽核使用。`raw_payload_json` 目前記錄連接器輸出的完整 normalized Document batch，並以 `payload_format=normalized-document-batch.v1` 明確標示；未宣稱它是 HTTP wire bytes。未來連接器若提供原始 body，可改存 object reference，但不得覆蓋既有事件。
