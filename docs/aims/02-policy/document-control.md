# AIMS 文件控制與 Evidence Manifest 草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-DOC-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立文件控制與 evidence manifest schema／not-applicable（初版） |
| Repository path | `docs/aims/02-policy/document-control.md` |

## 文件 metadata schema

每份受控 AIMS 文件最低須有：`document_id`、`title`、`version`、`status`、`owner`、
`approver`、`approval_record_uri`、`effective_date`、`review_date`、`next_review_date`、
`classification`、`change_summary`、`supersedes`、`repository_path`。

允許狀態：`draft-unapproved`、`in-review`、`approved-effective`、`superseded`、`withdrawn`。
空值必須明寫 `pending` 或 `not-applicable` 並附理由，不得把 merge、commit 或作者身分當作 approval。

## Evidence manifest schema

每個工作軌各自維護 manifest；整合軌最後彙總。每筆最低欄位：

| 欄位 | 說明 |
|---|---|
| `evidence_id` | 穩定唯一 ID |
| `control_or_requirement_id` | 所支援的 AIMS 要求、風險或控制 ID |
| `title` / `description` | 證據名稱及其實際證明範圍 |
| `source_class` | `normative`、`informative`、`internal-evidence` 之一 |
| `implementation_state` | `implemented`、`partially-implemented`、`planned-only`、`not-applicable` 之一 |
| `evidence_uri` | repo 相對路徑、固定 commit URI 或已核准 evidence store URI |
| `version_or_commit` | 可重現版本；草案可為 `working-tree-uncommitted` |
| `owner` | 產生與維護證據的角色 |
| `reviewer` / `review_status` | reviewer 與 `not-reviewed`、`accepted`、`rejected` |
| `captured_at` / `review_date` / `next_review_date` | ISO 8601 日期／時間 |
| `retention` / `classification` | 保存期限與存取分類 |
| `access_owner` / `authorized_audience` | 核准 evidence 存取者，以及獲授權角色／群組 |
| `integrity_proof` | 固定 commit、雜湊、簽章或受控儲存版本等完整性證明 |
| `redaction_status` | `not-required`、`pending`、`redacted`；並記錄遮罩 reviewer |
| `storage_class` | `repository-public`、`repository-restricted`、`approved-secure-store` 之一 |
| `limitations` | 不足、範圍外與不得推論事項 |
| `approval_record_uri` | 核准紀錄；未核准明寫 `pending` |

Evidence URI 必須由獲授權 reviewer 可定位；拒絕存取時應走受控 evidence access／temporary grant，
不得為方便審查複製到 repo 或非核准儲存。URI、title、description 與其他 metadata 本身不得洩漏 secret、
token、敏感內部識別碼、個資或敏感系統位置。可安全揭露的治理 ID（例如 `evidence_id` 與
`control_or_requirement_id`）及 repo 相對路徑不受此限，但不得由其值反推出 secret 或受限資產。
外部連結需保存必要 metadata；敏感 evidence target 必須位於
核准 secure store，依 classification 授權、遮罩及記錄存取，且不得把敏感內容放入 manifest。
客戶 PII 不得出現在 manifest、URI、repo、本機或測試 evidence copy；production evidence 的 reviewer
須使用受控存取並只保存非敏感證明。遮罩若仍可回復識別，不得視為可離開 production 的去識別資料。
文件、程式碼或測試存在只證明其存在，不自動證明控制設計適當、持續運作或有效。

## 控制流程（待核准）

Owner 起草 → 指定 reviewer 審查 → 有權者核准 → 記錄生效與散布 → 定期／事件觸發 review →
改版或標為 superseded／withdrawn。舊版須保留 lineage，修訂須說明變更與重新核准；保存與刪除期限
仍待適用法規、契約及資料分類評估後決定。
