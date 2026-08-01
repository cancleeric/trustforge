# AIMS 範圍草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-SCOPE-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 AIMS 範圍草案／not-applicable（初版） |
| Repository path | `docs/aims/01-scope/scope.md` |

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

## 適用環境與生命週期

| 環境 | 暫定範圍判定 | 可驗證依據／限制 |
|---|---|---|
| 本 repository 內的本機開發、測試與文件流程 | 納入 | `AGENTS.md`、`pyproject.toml`；不得帶入客戶 PII，只能使用合成或經核准且不可回復識別的資料；不推論任一人的私人裝置全機受 AIMS 管理 |
| GitHub issue／branch／PR 與 repo-defined review gate | 納入可由 HurricaneSoft 控制的流程 | `AGENTS.md`；GitHub Actions 已停用，不能宣稱其為有效控制 |
| AWS Bedrock 模型介面與供應商依賴 | 納入介面、資料交換及供應商治理 | `README.md`、`pyproject.toml`；帳號、region、實際啟用狀態待清冊確認 |
| App Runner／其他 production runtime | 暫定納入 TrustForge 實際受控 deployment；環境身分待確認 | `README.md` 僅載建議路線，不證明目前 production 位置或啟用狀態；客戶 PII 若存在，只能留在經核准的 production 邊界 |
| 第三方供應商內部訓練／基礎設施 | 排除直接控制；納入依賴風險 | HurricaneSoft 無直接控制權，仍須供應商評估 |
| 使用者自行交易、私人錢包／交易所環境 | 排除 | TrustForge 不控制該環境；可預見誤用影響仍納入評估 |

生命週期從構想到退役均納入；實際 production 身分、資料地域與 retention 是 scope 核准前的 blocker，
不得因「暫定納入」推論已完成清冊、控制有效或已覆蓋未知環境。

客戶 PII 禁止從 production 複製到 repo、本機、測試、tabletop 或稽核重演環境；需查證 production
evidence 時只能使用受控存取與非敏感證明。任何例外均須另經合規、法務與 CEO 明確核准，本草案
與其後續 merge／核准均不自動授權例外。

## 未決事項

正式核准前須確認法律實體、實際 production 環境、資料地域、客戶／使用者範圍、外部處理者、
法規義務及所有整合服務。任一變更須觸發 scope review 並留下核准紀錄。
