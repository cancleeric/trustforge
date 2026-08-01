# TrustForge EN 18286／EU AI Act 適用性與差距分析

> 文件狀態：Draft — preliminary gap assessment
>
> 日期：2026-08-01
>
> Parent tracking issue：#1264
>
> PR-A／本文件 slice：#1265
>
> 決策界線：本文件不是法律意見、合格評定、CE 標誌授權、ISO/EN 認證或符合性聲明。

## 1. 目的

本文件建立 TrustForge AIMS 文件草案與 Regulation (EU) 2024/1689（EU AI Act）之間的 preliminary assessment framework。EN 18286 的正式書目名稱、版本、publication 與 official status 均為 `authoritative source pending`，因此本文不把任何公開或二手名稱當作已確認的標準事實。

ISO/IEC 42001 AIMS 與 EU AI Act QMS 可能共用管理系統元件，但是否及如何映射到 EN 18286 必須等合法、權威版本後才能判斷。ISO/IEC 42001 本身不能取代 EU AI Act 的角色判定、風險分類、技術文件、合格評定及上市後義務。

## 2. 依據與證據限制

### 2.1 Claims and source register

| Claim | Authoritative source | Version／status | Accessed | Permitted reliance |
|---|---|---|---|---|
| Regulation (EU) 2024/1689 法規文字與條文 | EUR-Lex, CELEX `32024R1689`: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689 | OJ text at canonical CELEX URL; amendments／consolidation must be reverified | 2026-08-01 | 可作 preliminary article mapping；正式決策前重驗版本 |
| EN 18286 bibliographic title, edition, publication and official status | Authoritative official source pending | Pending | 2026-08-01 | 不可主張已發布、正式名稱或條文內容 |
| EN 18286 Official Journal citation／presumption of conformity | EUR-Lex OJ citation decision pending verification | No citation or presumption claimed | 2026-08-01 | 禁止 conformity／presumption claim |
| EN 18286 clause text | Company-licensed authoritative copy pending | Not acquired | 2026-08-01 | 禁止由搜尋摘要或二手資料重建／引用條款 |

### 2.2 受限事項

- EN 18286 完整條文屬受版權保護的標準內容；在取得公司合法授權版本前，不得由搜尋摘要、顧問文章或二手清單重建條文。
- 本文件只能進行法規層與標準目的層的 preliminary mapping。
- 條款級矩陣在合法取得標準全文前，一律標記 `awaiting licensed text`，不得標為 complete 或 conformant。
- 標準書目、發布、official status、EU Official Journal 協調標準引用與 presumption of conformity 狀態均為 pending；目前不作任何 OJ citation 或 presumption 主張。
- EU AI Act 時程僅作 planning checkpoint：Article 113 一般適用日為 2026-08-02；Article 6(1) 及相應義務為 2027-08-02。兩者都必須在使用前重驗 amendments、transitional provisions 與 Commission action，不得視為無條件結論。

## 3. TrustForge 預備分類

### 3.1 Proposed intended purpose

提案為：TrustForge 定位為具證據鏈、信任推理及報告輸出的加密市場分析 AI Agent，並排除特定自然人 Annex III 決策用途。這不是已驗證產品事實或核准的 intended purpose。產品行為、marketing claims、contracts、instructions、deployment 與 actual use 均為 `unknown / evidence pending`；README 或 repo 內自述不能自證。

### 3.2 尚未核准的初步判定

此 proposed purpose 不足以作 high-risk 或 non-high-risk 結論。Article 6、Annex I／III、角色與實際 EU facts 均待法律審查。以下情況觸發立即重新分類：

- 產品直接或實質決定自然人的信用、保險、就業、教育或基本服務權益；
- 成為受管制產品的安全元件；
- 客戶部署方式、行銷宣稱或實際使用超出已核准 intended purpose；
- 依主管機關、合格評定機構或法律意見確認屬 high-risk。

目前狀態為 `classification pending legal approval`。不得自行標示 non-high-risk、high-risk、conformant 或 CE-ready。

觸發後，所有受影響用途、release 與 contract 立即 blocked；已啟用路徑在安全及法律要求下停止／隔離。必須升級 Compliance Counsel、CISO、CPO、CEO，並在 exact product／contract／release version 的核准證據完成後才可解除。

### 3.3 經濟營運者角色待確認

需依實際 EU 上市與供應模式確認：

- HurricaneSoft 是否為 provider；
- 是否由 EU 境內 deployer 使用；
- 是否需要 authorised representative；
- 是否存在 importer、distributor、white-label 或重大修改者；
- 上游模型、資料提供者與雲端服務商的契約與證據責任。

Article 25 的 provider transition（例如以自己名稱上市、重大修改或改變 intended purpose）須獨立判定，不與 off-label actual misuse 或 reasonably foreseeable misuse 混為一談。Articles 17／72 是 high-risk provider 的條件式義務；Article 26 deployer monitoring／escalation 與 Article 73 provider reporting 應分開建模。Article 50 另作 feature、output 與 deployer transparency assessment，不是 risk-classification limb。

## 4. Unverified governance observations

Repository 中觀察到下列 AIMS 文件主張，但 artifact 是否存在於 exact commit、內容是否符合描述、owner、activation、核准與 effectiveness 均未驗證：

- AIMS scope；
- 組織情境與 interested parties；
- AI policy；
- roles、RACI 與 risk acceptance；
- document control；
- AI system inventory；
- 客戶 PII 僅限 production、不得離開 production 的無例外邊界。

上述僅是 unverified observations，不能先認定為控制或治理基線；目前全部仍是 `draft / unapproved / non-effective`，並不構成 ISO/IEC 42001、EN 18286 或 EU AI Act 符合性證據。

## 5. 初步差距矩陣

| 領域 | 現況 | 初步差距 | 處置 |
|---|---|---|---|
| EU market scope | AIMS scope artifact／activation／owner 未驗證 | EU territory、供應模式、版本與客戶用途邊界待證 | 建立 EU applicability record |
| Intended purpose | 市場分析定位只是未驗證文件主張 | 產品、marketing、contract、deployment facts 與正式敘述待核准 | 建立受控 proposed intended-purpose statement |
| Operator role | EU operator register existence 未驗證 | provider／deployer／representative 等角色未核准 | 建立 operator-role assessment |
| Risk classification | classification record existence 未驗證 | Article 6、Annex I／III 待評估；Article 50 另案 | 建立法律審查與變更觸發條件 |
| Article 17 QMS | ISO 42001 文件路徑 observed only | high-risk provider applicability 與 evidence 待核准；不先評 maturity | 條款級映射待合法全文與角色判定 |
| Risk management | #1244 僅為引用，狀態／內容未驗證 | Article 9 high-risk provider applicability 與 evidence 待核准 | 分開記錄風險、可合理預見誤用與殘餘風險 |
| Data governance | production-only PII 邊界是未驗證主張 | Article 10 僅在適用條件成立時評估，且須區分採用模型訓練技術與未採用者；PII 邊界不等於 Article 10 compliance | 先確認分類、技術與 actor，再建立資料治理證據 |
| Technical documentation | 架構、QA、證據文件是未驗證 observation | Article 11／Annex IV applicability、exact artifact 與 release binding 待證 | 建立版本化 technical file index |
| Record keeping | 執行 log 與 Evidence 機制是未驗證 observation | 適用性、exact artifact、activation、owner、保存、完整性、存取與 retention 均待證 | 建立並驗證 logging／retention control |
| Instructions and transparency | 產品與 evidence UI 文件是未驗證 observation | Article 13 applicability、exact artifacts、activation、限制與監督方式待證 | 建立 EU instructions package |
| Human oversight | 人為核准與 formal-run 邊界是未驗證 observation | applicability、artifact、activation、能力、介入與停止條件均待證 | 建立 oversight plan 與演練證據 |
| Accuracy, robustness, cybersecurity | 測試、安全與 fail-closed 是未驗證 claims | applicability、exact controls、activation、threshold、限制與 release 證據待證 | 建立性能／韌性／資安基準 |
| Article 50 transparency | 尚未獨立評估 feature、output、provider／deployer facts | 不得併入 Article 6 risk classification | 建立獨立 Article 50 assessment |
| Conformity assessment | 尚未建立 | 缺適用途徑、責任人、notified body 判定及變更再評估 | 法律核准後建立流程 |
| Registration and CE | 尚未建立 | 不得提前註冊、加 CE 或宣稱 presumption of conformity | 維持 blocked，直到適用性與程序核准 |
| Post-market monitoring | #1245 狀態／內容未驗證 | Article 72 對 high-risk provider 的適用性與角色待核准 | 建立 actor-specific PMS／CAPA 計劃 |
| Serious incident reporting | EU workflow existence 未驗證 | Article 73 provider reporting 與其他 actors 的 escalation interface 待分工 | 建立 actor-specific 法規事件 playbook |
| Supplier control | inventory 的 existence／content／owner 未驗證 | 模型、資料、雲端等供應者責任、變更與證據要求待證 | 建立 supplier register 與契約控制 |
| EN 18286 clauses | 未持有合法全文 | 無法可靠完成逐條核對 | 全數 `awaiting licensed text` |

## 6. 改善工作包

### Phase 0 — Authority and source

Phase 0 未完成，且目前為 `blocked — owner／ticket／evidence／due state pending`。不得把本文件或 source register 視為已取得合法全文。

1. Proposed owner：Compliance Counsel；ticket：待在 #1264 建立／連結；evidence：採購或授權紀錄、正式來源、版本、語言及使用範圍；due state：`blocked until owner accepts and ticket records due date`。
2. 取得 EN 18286 合法授權全文並記錄版本、語言、來源與授權範圍。
3. 由 Compliance Counsel 核准 EU AI Act 適用法規版本及角色／分類方法。
4. 明確指定文件 owner、approver 與 review cadence。

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

#1264 是父 issue；#1265／PR-A 僅涵蓋 preliminary documents，不得用 PR-A merge／close 語意關閉父 issue 或宣稱 Phase 0–4 完成。

父 issue 的第一階段驗收條件（均須在 #1264 個別驗證）：

- 本分析報告納入版控並完成來源與限制審查；
- intended purpose、operator role、risk classification 均有 owner 與核准狀態；
- 建立合法 EN 18286 全文取得工作項，不把二手摘要當條文；
- 建立四向矩陣骨架，EN 條款維持 `awaiting licensed text`；
- 明確連結 #1244、#1242、#1243、#1245、#1246；
- 所有文件維持 draft、unapproved、non-effective；
- 沒有 DB、migration、secret、IAM、部署或應用程式碼異動。

## 8. CEO 決策建議

核准先執行 Phase 0–1 文件工作，不核准任何符合性、CE、high-risk／non-high-risk 最終聲明或產品部署變更。取得合法 EN 18286 全文及 Compliance Counsel 分類意見後，再核准 Phase 2 條款級 QMS overlay。
