# AIMS 範圍草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-SCOPE-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| Review / next review | 待核准時設定／待核准時設定 |

## 目的與邊界

本草案建議 AIMS 涵蓋 HurricaneSoft 對 TrustForge Hermes 加密市場分析 AI Agent 的設計、
開發、測試、發布、運行監測、變更、事件處理與退役治理，以及其分析所使用的資料、模型、
供應商、人工決策和可追溯輸出。組織邊界限於 HurricaneSoft 對上述活動具有控制或能施加影響
的部分。最終範圍需由 CEO 核准後才生效。

## 明確納入

- TrustForge repo 內的 Hermes 分析管線、Trust Layer、Agent 編排、Web/API 與正式交付件。
- AWS Bedrock 模型使用介面，以及模型選擇、成本／停止控制、輸出限制與供應商監督。
- 來源資料、OHLCV、樣本資料、衍生特徵、校準模型與其 lineage／保存／品質控制。
- Evidence List、Execution Log、分析報告與人工覆核、例外、incident、CAPA 的治理流程。
- issue、branch、測試、pre-push、PR、對抗式審查、release 與 production verification gate。
- 對使用者、團隊成員、資料提供者、競賽主辦方及受分析內容影響者的相關 AI 影響。

## 明確排除

- HurricaneSoft 無控制權的 AWS Bedrock 基礎模型訓練、AWS 內部基礎設施與第三方來源內容本身；
  其依賴與風險仍須列入供應商／資料治理。
- 使用者在 TrustForge 輸出之外自行作成的交易或投資決策；系統輸出風險與可預見誤用仍納入。
- 其他 HurricaneGroup 公司、產品、共用服務或內部 AI 系統，除非其成為 TrustForge 已核准依賴。
- ISO/IEC 42001 第三方稽核、驗證或認證活動；是否進入認證由後續管理審查決定。
- 競賽規則本身；競賽義務另依 `docs/competition/COMPETITION-OFFICIAL.md` 追溯。

## 適用地點與生命週期

文件與程式碼以此 repository 為目前可驗證邊界；實際部署地點、營運環境、資料地域與 retention
尚待資產及供應商盤點確認。生命週期從構想到退役均納入，但本文件不宣稱各階段控制已有效實作。

## 未決事項

正式核准前須確認法律實體、實際 production 環境、資料地域、客戶／使用者範圍、外部處理者、
法規義務及所有整合服務。任一變更須觸發 scope review 並留下核准紀錄。
