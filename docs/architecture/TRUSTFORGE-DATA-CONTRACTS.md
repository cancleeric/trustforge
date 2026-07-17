# TrustForge 核心資料契約

TrustForge 的 `Document`、`Evidence`、`Report` 是來源層、信任核心與交付層之間的正式契約。每個 payload 都必須攜帶 `schema_version`；目前三者皆為 `1.0.0`。

機器可讀契約存放於 `docs/contracts/trustforge-data-contracts-v1.json`，由 `scripts/check_data_contracts.py` 從程式碼的單一事實來源產生及驗證。CI 會拒絕契約檔與程式碼不一致的變更。

## 所有權與消費者

| 契約 | Owner | 主要消費者 | 保存與 SLA |
|---|---|---|---|
| Document | Data Plane | normalization、snapshot、analysis scheduler | 原始事件永久 append-only；最新快取不視為歷史真相 |
| Evidence | Trust Kernel | trust inference、audit、UI | 跟隨 run 與 snapshot 保存，不得失去來源回溯資訊 |
| Report | Delivery | API、Web UI、audit export | 每個已完成 run 固定版本、不可覆寫 |

## 相容性規則

- 同一 major version 可新增選填欄位，不得刪除欄位、移除 required 或改變既有型別。
- breaking change 必須提高 major version，保留舊 schema，並提供 migration/dual-read 期間。
- 舊快取缺少 `schema_version` 時，讀取器只為既有 v1 資料補 `1.0.0`；新寫入一律顯式保存版本。
- Trust Kernel 的正式輸入輸出只能經過契約驗證後進入下一層。
