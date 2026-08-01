# 組織情境與利害關係人登錄草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-CTX-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| Review / next review | 待核准時設定／待核准時設定 |

## 內外部議題

| ID | 類型 | 議題 | AIMS 影響 | 來源／證據 | Owner | Last review | Next review | 狀態 |
|---|---|---|---|---|---|---|---|---|
| CTX-01 | 內部 | 單一開發者仍須完成完整 review/release gate | 權責分離與 reviewer 獨立性需明確 | `AGENTS.md` | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| CTX-02 | 內部 | 系統強調 evidence、execution log、反方證據及 lineage | 可作追溯基線，但不等同控制有效 | `README.md` | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| CTX-03 | 內部 | 部分 learning／AGOS 能力已實作但 production 未啟用 | 清冊必須分開 implemented、enabled、observed | `README.md` | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| CTX-04 | 外部 | 基礎模型由 AWS Bedrock 提供 | 需供應商、可用性、成本及模型變更治理 | `README.md`、`pyproject.toml` | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| CTX-05 | 外部 | 加密市場資訊高噪音、可能矛盾或被操縱 | 需來源信任、限制揭露與 human oversight | `README.md` | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| CTX-06 | 外部 | 競賽要求與 AIMS 改善目的不同 | 必須保持兩條 traceability，不作認證推論 | `docs/competition/COMPETITION-OFFICIAL.md`、本 AIMS 文件集 | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |
| CTX-07 | 外部 | AI、金融資訊、隱私、著作權與供應商義務可能變動 | 需由合規 owner 建立適用義務清冊 | 待合規評估 | 待 CEO 指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |

## 利害關係人與需求

| ID | 利害關係人 | 需求／期望 | 需求來源／證據 | AIMS 回應與驗證 | Owner | Last review | Next review | 狀態 |
|---|---|---|---|---|---|---|---|---|
| STK-01 | 使用者／分析讀者 | 可追溯、限制清楚、不以 AI 取代決策 | `README.md`；使用者研究待補 | evidence、信心、反方證據、人工監督；待使用者驗證 | 待指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| STK-02 | CEO／HurricaneSoft | 風險、成本、發布與聲明可控 | `AGENTS.md` | RACI、風險接受、release gate、管理審查 | CEO（待核准） | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| STK-03 | 開發與維運角色 | 明確需求、變更界線、可重演 evidence | `AGENTS.md` | issue/PR/gate、文件控制、清冊 | 待指派 | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| STK-04 | 資料主體／內容作者／資料提供者 | 合法、適當、可追溯的資料處理與引用 | 待合規／利害關係人訪談確認 | 資料清冊、權利基礎、retention、移除流程 | 待合規指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |
| STK-05 | AWS／其他供應商 | 契約、可接受使用與技術限制被遵守 | 合約／服務條款待正式盤點 | due diligence、合約與變更 review | 待指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |
| STK-06 | 競賽主辦方／HOYA BIT | 符合官方交付與資料規範 | `docs/competition/COMPETITION-OFFICIAL.md` | 獨立 competition traceability | 競賽 owner（待確認） | 2026-08-01 初始盤點 | 待核准時設定 | 部分實作 |
| STK-07 | 合規、法務與 reviewer | 不誤導的公開聲明、可查驗紀錄 | Issue #1247 reviewer route；正式訪談待補 | 聲明核准 gate、稽核 trail | 待指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |
| STK-08 | 受錯誤或誤用影響的個人／群體 | 避免傷害、申訴與事件處理 | 待 impact assessment／利害關係人確認 | impact assessment、incident/CAPA、申訴入口 | 待指派 | 2026-08-01 初始盤點 | 待核准時設定 | 僅計劃 |

至少每年、發生重大系統／法規／供應商／用途變更或重大 incident 後 review；頻率及 owner 待核准。
