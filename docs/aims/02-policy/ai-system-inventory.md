# AI 系統／資產清冊草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-INV-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立清冊 schema 與 TrustForge 初始登錄／not-applicable（初版） |
| Repository path | `docs/aims/02-policy/ai-system-inventory.md` |

## 最低欄位

每項 AI 系統須記錄：穩定 ID、名稱／版本、owner、業務目的、預期／禁止用途、使用者與受影響者、
生命週期狀態、implemented/enabled/observed 狀態、部署環境／地域、AI 技術與模型／供應商、輸入／輸出、
資料來源與分類、關鍵依賴、human oversight、風險／影響評估 ID、控制／incident ID、監測指標、
變更／退役方式、evidence URI、last/next review、核准狀態。未知值不得猜測，須明寫 `待確認`。

## 初始登錄：AIMS-AIS-001

| 欄位 | 已查證內容 |
|---|---|
| 名稱／版本 | TrustForge Hermes（repo dynamic version；本草案未解析 release 版本） |
| Owner／核准 | HurricaneSoft 出品；system owner 待 CEO 正式指派；清冊未核准 |
| 目的 | 對加密市場多源資訊進行信任提煉，輸出帶信任權重與溯源的市場分析 |
| 預期使用者／受影響者 | 分析讀者及競賽展示使用者；完整群體與間接受影響者待 impact assessment |
| 預期用途 | 多源整合、假設驗證、比較分析；提供可查證資料以協助人的判斷 |
| 禁止用途 | 尚無已核准清單；不得把輸出描述為取代人的投資決策（政策草案，待核准） |
| 技術／模型／供應商 | Python 應用；AWS Bedrock runtime 是 repo 宣告的模型入口；具 Isotonic Regression 信心校準 |
| 輸入 | 新聞／RSS、社群、鏈上、監管／公告、OHLCV 等設計來源；實際 connector 啟用狀態須逐項盤點 |
| 輸出 | `report.md`、`evidence.json`、`execution_log.jsonl`，以及 Web/API 分析呈現 |
| Human oversight | README 描述升級候選需人工批准；完整操作責任、介入與 override 證據待盤點 |
| 生命週期狀態 | 部分實作；README 明載部分 three-track learning／AGOS 能力尚未在 production 啟用 |
| 部署／地域 | repo 記載本機 runtime 與 AWS App Runner 建議路線；實際 production deployment／地域待確認 |
| 資料分類／retention | 待資料清冊與合規評估，不由本草案推定 |
| 風險／影響 | register 與 completed impact assessment 尚待後續工作軌建立 |
| 監測／incident／退役 | 有 repo 所述品質、可靠性、budget 與停止能力；實際運作證據及退役程序待盤點 |
| 可驗證 evidence URI | `README.md`、`pyproject.toml`、`docs/architecture/ARCHITECTURE.md`、`AGENTS.md` |
| Last / next review | 2026-08-01（初始 repo 盤點）／待核准時設定 |

此項登錄只摘要上述檔案所述事實，不證明 production 啟用、控制有效、法規符合或 ISO/IEC 42001 認證。
