# AIMS 角色、RACI 與殘餘風險接受草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-RACI-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立角色、RACI 與殘餘風險接受草案／not-applicable（初版） |
| Repository path | `docs/aims/02-policy/roles-raci-and-risk-acceptance.md` |

R = 執行，A = 最終負責／核准，C = 諮詢，I = 知會。下表是建議介面，不代表人員已接受任命。

| 活動 | CEO | AIMS manager（待指派） | System owner（待指派） | CISO | CPO | 合規／法務 | 獨立 reviewer |
|---|---|---|---|---|---|---|---|
| 核准 scope、policy、目標 | A | R | C | C | C | C | I |
| 維護清冊與文件控制 | I | A/R | R | C | C | C | I |
| 風險／影響評估與處置 | I | A | R | C | C | C | C |
| 安全敏感變更 | I | C | R | A | C | I | C |
| 產品用途與 human oversight | I | C | R | C | A | C | C |
| AI／安全 incident 宣告、停損與證據保全 | I（重大安全事件須立即通知） | C | R | A | C | C | I |
| 供應商風險、變更與供應商相關事件 | I | A | R | C | C | C | I |
| 對外 AIMS／ISO 聲明 | A | R | I | C | C* | C* | I |
| 內部稽核／readiness review | I | C | C | C | C | C | A/R* |
| 管理審查 | A | R | C | C | C | C | I |

\* reviewer 不得稽核自己的實作；無獨立人員時只能稱 gap/readiness review，不能稱獨立內部稽核。

每項活動只能有一個 `A`。對外 AIMS／ISO 聲明雖由 CEO 最終核准，仍必須取得 CPO 與合規／法務
會簽（表中 `C*`）；缺任一必要會簽不得發布。重大安全事件須立即通知 CEO，不因 CEO 在該活動為
`I` 而延後。上述角色與聯絡替代人仍待 CEO 正式指派及核准。

## 殘餘風險接受權限

| 等級 | 建議接受權限 | 上線條件 |
|---|---|---|
| 低 | System owner | 評估、owner、review date 與 rationale 完整 |
| 中 | AIMS manager；安全／產品／合規面向另由相應角色會簽 | 處置、監測、期限及回復方式完整 |
| 高 | CEO；並由 CISO、CPO、合規／法務按風險面向會簽 | 原則上先降低；例外接受須有時限及加強監測 |
| 極高／不可容忍 | 不得由單一角色接受 | 停止／不得發布，直至降低至已核准容忍度 |

所有門檻與人員任命均待 CEO 核准。接受紀錄最低包含 risk ID、固有／殘餘等級、處置與未處置理由、
適用義務、影響對象、補償控制、evidence URI、接受者、決策日期、到期／review date。沉默、文件缺值、
排程壓力或競賽期限都不是風險接受。
