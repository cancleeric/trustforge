# TrustForge EN 18286／EU AI Act 適用性與差距分析

> 文件狀態：Draft — preliminary gap assessment
>
> 日期：2026-08-01
>
> Tracking issue：#1264
>
> 決策界線：本文件不是法律意見、合格評定、CE 標誌授權、ISO/EN 認證或符合性聲明。

## 1. 目的

本文件評估 TrustForge 現有 ISO/IEC 42001 AI management system（AIMS）治理基線，與 EN 18286「Artificial intelligence — Quality management system for EU AI Act regulatory purposes」及 Regulation (EU) 2024/1689（EU AI Act）之間的關係、適用性問題與初步差距。

EN 18286 是歐洲針對 EU AI Act 法規目的建立的 AI quality management system（QMS）標準。ISO/IEC 42001 是通用 AIMS 標準；兩者可以共用管理系統元件，但 ISO/IEC 42001 本身不能取代 EU AI Act 的角色判定、風險分類、技術文件、合格評定及上市後義務。

## 2. 依據與證據限制

### 2.1 已確認的公開依據

- Regulation (EU) 2024/1689 為 EU AI Act 正式法規。
- EU AI Act Article 17 要求 high-risk AI system provider 建立、文件化並維持品質管理系統。
- EN 18286 的公開名稱及目的指向 EU AI Act regulatory purposes 的 AI QMS。
- CEN-CENELEC JTC 21 負責回應 EU AI Act 的 AI 標準化工作。

### 2.2 受限事項

- EN 18286 完整條文屬受版權保護的標準內容；在取得公司合法授權版本前，不得由搜尋摘要、顧問文章或二手清單重建條文。
- 本文件只能進行法規層與標準目的層的 preliminary mapping。
- 條款級矩陣在合法取得標準全文前，一律標記 `awaiting licensed text`，不得標為 complete 或 conformant。
- 標準發布狀態、EU Official Journal 協調標準引用與 presumption of conformity 狀態必須在正式 readiness 決策前重新核對。

## 3. TrustForge 預備分類

### 3.1 Intended purpose

TrustForge 是具證據鏈、信任推理及報告輸出的加密市場分析 AI Agent。現有公開用途不是人員招募、教育錄取、執法、移民、司法、關鍵基礎設施安全控制，亦不應用於自然人信用評分或取得基本公共／私人服務資格的自動決定。

### 3.2 尚未核准的初步判定

依目前 intended purpose，TrustForge 不應只因為使用 AI 或分析金融市場，就自動宣稱屬 EU AI Act Annex III high-risk AI system。以下情況會改變結論，必須重新分類：

- 產品直接或實質決定自然人的信用、保險、就業、教育或基本服務權益；
- 成為受管制產品的安全元件；
- 客戶部署方式、行銷宣稱或實際使用超出已核准 intended purpose；
- 依主管機關、合格評定機構或法律意見確認屬 high-risk。

目前狀態為 `classification pending legal approval`。不得自行標示 non-high-risk、high-risk、conformant 或 CE-ready。

### 3.3 經濟營運者角色待確認

需依實際 EU 上市與供應模式確認：

- HurricaneSoft 是否為 provider；
- 是否由 EU 境內 deployer 使用；
- 是否需要 authorised representative；
- 是否存在 importer、distributor、white-label 或重大修改者；
- 上游模型、資料提供者與雲端服務商的契約與證據責任。

## 4. 現有治理基線

已合併的 TrustForge AIMS 基線包含：

- AIMS scope；
- 組織情境與 interested parties；
- AI policy；
- roles、RACI 與 risk acceptance；
- document control；
- AI system inventory；
- 客戶 PII 僅限 production、不得離開 production 的無例外邊界。

上述內容可作為共用管理系統地基，但目前全部仍是 `draft / unapproved / non-effective`，並不構成 ISO/IEC 42001 或 EN 18286 符合性證據。

## 5. 初步差距矩陣

| 領域 | 現況 | 初步差距 | 處置 |
|---|---|---|---|
| EU market scope | AIMS scope 尚未形成 EU 上市邊界 | 缺 EU territory、供應模式、版本與客戶用途邊界 | 建立 EU applicability record |
| Intended purpose | 產品文件有市場分析定位 | 尚未形成受控、可供分類與技術文件引用的正式敘述 | 建立受控 intended-purpose statement |
| Operator role | 尚無 EU operator register | provider／deployer／representative 等角色未核准 | 建立 operator-role assessment |
| Risk classification | 尚無 EU AI Act classification record | 缺 Article 6、Annex I／III、Article 4／50 等適用性分析 | 建立法律審查與變更觸發條件 |
| Article 17 QMS | ISO 42001 治理基線為 draft | 缺 EU AI Act 專用政策、程序、責任及法規交付證據 | 建立 EN 18286 overlay；條款級映射待合法全文 |
| Risk management | #1244 尚未執行 | 缺 EU AI Act Article 9 生命週期風險管理閉環 | 納入風險、可合理預見誤用與殘餘風險 |
| Data governance | 有 production-only PII 邊界 | 缺 Article 10 dataset relevance、representativeness、error、bias 與 provenance 記錄 | 建立資料治理與資料限制證據 |
| Technical documentation | 有分散的架構、QA、證據文件 | 缺 Article 11／Annex IV 對照與 release-bound 技術檔案 | 建立版本化 technical file index |
| Record keeping | 有執行 log 與 Evidence 機制 | 缺 Article 12 法規保存、完整性、存取與 retention 決策 | 建立 logging／retention control |
| Instructions and transparency | 有產品與證據 UI 文件 | 缺 Article 13 受控 instructions for use 及限制、輸入規格、監督方式 | 建立 EU instructions package |
| Human oversight | 有人為核准與 formal-run 邊界 | 缺 Article 14 oversight measure、能力、介入與停止條件的正式測試 | 建立 oversight plan 與演練證據 |
| Accuracy, robustness, cybersecurity | 有測試、安全與 fail-closed 控制 | 缺 Article 15 法規需求、threshold、已知限制與 release 證據矩陣 | 建立性能／韌性／資安基準 |
| Conformity assessment | 尚未建立 | 缺適用途徑、責任人、notified body 判定及變更再評估 | 法律核准後建立流程 |
| Registration and CE | 尚未建立 | 不得提前註冊、加 CE 或宣稱 presumption of conformity | 維持 blocked，直到適用性與程序核准 |
| Post-market monitoring | #1245 尚未執行 | 缺 Article 72 監測計劃、事件趨勢、CAPA 與回饋閉環 | 建立 PMS／CAPA 計劃 |
| Serious incident reporting | 尚無 EU AI incident workflow | 缺 Article 73 判定、時限、保存、通報與演練 | 建立法規事件 playbook |
| Supplier control | inventory 尚未完成供應鏈證據化 | 缺模型、資料、AWS／Bedrock 等供應者責任、變更與證據要求 | 建立 supplier register 與契約控制 |
| EN 18286 clauses | 未持有合法全文 | 無法可靠完成逐條核對 | 全數 `awaiting licensed text` |

## 6. 改善工作包

### Phase 0 — Authority and source

1. 取得 EN 18286 合法授權全文並記錄版本、語言、來源與授權範圍。
2. 由 Compliance Counsel 核准 EU AI Act 適用法規版本及角色／分類方法。
3. 明確指定文件 owner、approver 與 review cadence。

### Phase 1 — Applicability and classification

1. 建立 intended-purpose statement。
2. 建立 operator-role assessment。
3. 建立 EU AI Act classification record 與重新分類觸發條件。
4. 定義禁止用途、合理可預見誤用及部署契約邊界。

### Phase 2 — QMS overlay

1. 在現有 AIMS 上建立 EN 18286 overlay，而非複製第二套互相矛盾的管理系統。
2. 建立 Article 17 QMS policy、procedures、RACI、document control 與 evidence index。
3. 取得合法全文後完成 EN 18286 ↔ EU AI Act ↔ ISO/IEC 42001 ↔ TrustForge evidence 四向矩陣。

### Phase 3 — Product and lifecycle evidence

依 #1244、#1242、#1243、#1245、#1246 完成 risk、support、lifecycle、measurement/CAPA、SoA/readiness；增加 EU technical documentation、instructions、logging、human oversight、accuracy／robustness／cybersecurity、supplier、PMS 及 incident controls。

### Phase 4 — Independent readiness decision

1. 完成 CPO、CISO、Compliance Counsel 及 adversarial review。
2. 對 exact release commit 執行完整本地 gate 與證據對帳。
3. 未取得法律核准、所需合格評定及協調標準狀態確認前，維持 `not ready for EU conformity claim`。

## 7. 建議 issue 與依賴

建立一張父 issue：`EN18286-EUAI: 建立 EU AI Act QMS overlay 與適用性證據`。

父 issue 第一階段驗收條件：

- 本分析報告納入版控並完成來源與限制審查；
- intended purpose、operator role、risk classification 均有 owner 與核准狀態；
- 建立合法 EN 18286 全文取得工作項，不把二手摘要當條文；
- 建立四向矩陣骨架，EN 條款維持 `awaiting licensed text`；
- 明確連結 #1244、#1242、#1243、#1245、#1246；
- 所有文件維持 draft、unapproved、non-effective；
- 沒有 DB、migration、secret、IAM、部署或應用程式碼異動。

## 8. CEO 決策建議

核准先執行 Phase 0–1 文件工作，不核准任何符合性、CE、high-risk／non-high-risk 最終聲明或產品部署變更。取得合法 EN 18286 全文及 Compliance Counsel 分類意見後，再核准 Phase 2 條款級 QMS overlay。
