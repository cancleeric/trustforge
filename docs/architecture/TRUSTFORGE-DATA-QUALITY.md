# 資料品質閘與 Quarantine

所有真實 connector batch 在寫入 Bronze `source_events` 與 latest `connector_cache` 前，先經過 deterministic quality gate：

- `schema_version` 必須符合目前 Document v1 契約。
- id、source、kind、text 不可空白。
- timestamp 必須有限、非負且不可超前目前時間超過五分鐘。
- 同一 batch 的 document id 與 source/url/text 內容不可重複。

未通過的 record 會 append 到不可變 `quarantined_source_records`，保存原因碼、原 Document、hash、source、coin、scheduler run 與時間。部分 batch 有合格資料時只讓合格資料繼續；整批均不合格時，本輪失敗且不覆蓋舊 cache。這確保資料品質異常可觀察、可稽核，同時不污染 snapshot 與核心推理。
