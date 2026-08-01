# TrustForge AI 政策草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-POL-001 |
| 版本／狀態 | 0.1-draft／草案、未核准、未生效 |
| Policy owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 AI 政策與最低升級路徑草案／not-applicable（初版） |
| Repository path | `docs/aims/02-policy/ai-policy.md` |

HurricaneSoft 擬以風險為本、可追溯且以人負責的方式設計及運行 TrustForge。正式核准後，
適用範圍內的角色應遵循下列原則：

1. AI 用途、預期使用者、限制與禁止用途須被記錄；輸出協助判斷，不取代人的投資決策責任。
2. 重要主張須保留來源、取得時間、內容參照及相關 claim；矛盾與低信任證據不得靜默刪除。
3. 依風險及影響程度安排人工監督、測試、停止、回復、事件通報與變更核准。
4. 資料與模型須有 owner、來源／供應商、權利與品質、版本、retention 和 lineage 記錄。
   客戶 PII 只能留在經核准的 production 環境；本機、測試、tabletop 與稽核重演只能使用合成資料，
   或經核准且不可回復識別的資料。不得以方便開發、review 或 evidence 重演為由跨環境複製客戶 PII。
5. 對安全、隱私、公平、透明、可用性、財務及個人／群體／社會影響進行評估與處置。
6. 未完成必要處置的殘餘風險只能由有權者明確接受；缺 owner、期限或接受紀錄不得上線。
7. 能力、權限及責任須與角色匹配；任何人不得偽造 reviewer 獨立性或 approval evidence。
8. incident、complaint、不符合與控制失敗須可追蹤至修正、根因、CAPA 及有效性檢查。
9. 透過目標、監測、內部 gap/audit、管理審查與矯正措施持續改善 AIMS。
10. 公開聲明須經法務／合規核准；取得有效第三方證書前，不得宣稱「ISO/IEC 42001 certified」
    或無保留的「compliant」。本政策存在本身不證明符合性或控制有效。

例外須記錄範圍、理由、風險、補償控制、到期日、owner 與核准者。重大違反應停止受影響活動並依
incident 流程處理；具體分級與時限由後續已核准程序定義。

## 最低升級路徑草案（未核准、未生效）

- 任何發現者立即通知 System owner 與 AIMS manager；涉及或疑似重大安全事件時，立即通知 CISO
  與 CEO，不等待完整根因或後續程序完成。
- CISO 對安全事件負責事件宣告與安全停損決策，System owner 執行隔離、停止受影響流程、保存
  evidence 與維持 chain of custody；必要時 CEO 可直接下令停止。
- 涉及產品用途、使用者傷害或輸出品質者升級 CPO；涉及法律、隱私、著作權、契約或主管機關義務者
  升級合規／法務；涉及可能罰鍰、訴訟、主管機關調查／法定通報或其他重大合規風險者，須立即
  同步升級合規／法務與 CEO。高／極高殘餘風險與跨面向重大事件亦升級 CEO 決策。
- 涉及供應商系統、資料或契約義務者，由指定 supplier owner 協調 CISO／合規／法務評估通報與
  停用邊界；不得在未核准下把敏感 evidence 或 secret 傳給供應商。
- 正式分級、時限、聯絡資料與替代人員仍須由後續程序核准；本草案不能代替有效 incident procedure。
