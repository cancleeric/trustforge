# Trust Feature Store

`trust_feature_values` 是 append-only、point-in-time correct 的信任特徵儲存層。每個特徵同時保存：

- `event_time`：資料所代表的事件時間。
- `available_at`：Hermes 真正取得並可使用該資料的時間。
- feature set／name／entity、schema version。
- snapshot、run、source reference lineage。

`get_as_of()` 同時要求 `event_time <= as_of` 與 `available_at <= as_of`，因此 replay 或日後 fitting 不會看到當時尚未取得的資料。表上 trigger 禁止更新與刪除。

Hermes 每次 `result_published` 會原子寫入 `analysis_trust.v1`：校準後信心、裸信心、Evidence 數、平均 Evidence trust、獨立來源數；所有值與該次 snapshot/run 固定綁定。這是未來每日 replay、外框策略校準與離線評估的正式輸入，不再從 UI 數字反推特徵。
