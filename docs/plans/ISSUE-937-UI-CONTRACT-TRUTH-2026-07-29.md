# Issue #937 — UI Contract Truth

日期：2026-07-29

狀態：現況稽核，供 CEO Gate A 審查；本文件本身不代表 Gate A 通過

基準：`origin/develop` (`41d1bb2e`)

## 1. 目的與誠實邊界

本文件只記錄目前程式碼、API、型別與測試已經支持的事實，以及 UI/UX
後續工作必須補齊的缺口。它不是未來 schema 的承諾，也不得被用來宣稱
尚未實作的 Hermes intent-planning API/provider integration、穩定 claim
identity、正式 run idempotency receipt、公開 log policy 或完整競賽包已存在。

官方「多源整合、假設驗證、比較分析」三種題型只作競賽驗證案例，不是
Hermes UI、輸入或 API 可接受問題的白名單。使用者輸入應維持任意自然語言；
不得以 selector、client validation 或 server validation 將輸入限縮為三題型。
但是「自由文字可輸入」不等於後端已存在任意／混合 intent 的結構化 contract。
本段取代已關閉 #935 所依據的舊三題型 UI 計畫。

## CEO Gate A decisions

以下方向已決，不再列為 unresolved：

- Intent preview：#955（C03A contract）→ #956（control-plane/provider/API
  integration）→ #939（UI）。#956 的 durable admission control-plane 基礎元件
  已分批落地，但不等於可供 UI 呼叫的 planning endpoint 已存在。
- Formal run identity/fresh rerun：#957（C04A contract）→
  #958（implementation）→ #940（UI）。
- Claim identity/schema：#959（contract）→ #960（implementation）→
  #941/#942/#949。
- Artifact policy：不新增 server-side immutable artifact。競賽交付採現有
  client report/evidence/log、#949 Evidence CSV parity、#944 delivery hub，
  加上 repository submission pack / Source-Config。
- Public log policy：#943 的 reviewer/public view 採 allowlist，明確排除 raw
  question 與 connector params。完整 audit artifact 只允許受控、非公開；
  是否另開 issue 由 #943 security review 判定。

## 2. Contract truth summary

| 領域 | Current truth | 不可宣稱 | Gap owner / follow-up |
|---|---|---|---|
| 任意自然語言問題 | `QueryConsole` 的 textarea 接受自由文字並送出 `q`；元件已移除三題型 selector，註解明載官方三種只是範例（`frontend/src/components/QueryConsole.tsx:64-84,152-160`）。正式 registration 再送出 `question`（`frontend/src/lib/endpoints.ts:104-130`）。 | 不可宣稱 Hermes 已回傳任意 intent taxonomy 或 combined-intent plan；不可把官方三案例變成 UI/input whitelist。 | #955 → #956 → #939；#953 驗證官方案例與 mixed/unknown。 |
| intent preview | 正式 UI flow 的 registration body 仍只有 `coin/mode/question/locale`，receipt 仍只有 `question_id/job_id/state/origin`（`frontend/src/lib/endpoints.ts:104-130`），沒有 UI 可呼叫的 planning endpoint/payload。另一方面，trusted clock、admission compiler/executor、durable gate、terminal reconcile、lease recovery 已是實際 control-plane 元件（`src/trustforge/preview_trusted_clock.py`; `preview_admission_compiler.py`; `preview_admission_executor.py`; `preview_durable_admission_gate.py`; `preview_terminal_reconcile.py`; `preview_lease_recovery.py`）。 | 不可把 control-plane primitives 說成完整 Hermes preview API/provider integration，也不可用前端猜測文字冒充模型理解結果。 | #955、#956 仍 OPEN；#967/#973/#983/#991/#992 與其 merged implementation PR 已完成基礎切片；#939 仍依賴可消費的 server contract。 |
| question type / combined intent | 後端 `QuestionType` 仍只有 `multi_source/hypothesis/comparison`（`src/trustforge/schema.py:26-29`）；手動 flow 的 mode 映射仍只產生這些 enum（`src/trustforge/analysis_flow.py:68`）。 | 不可宣稱後端已有 `combined` enum，亦不可把三 enum 當使用者輸入限制。 | #955 → #956 → #939；comparison 仍是獨立雙資產流程。 |
| 手動正式 flow 的內容去重 | UI 真正 POST `/api/analysis-question`，handler 呼叫 `AnalysisFlow.submit_manual`（`frontend/src/pages/AnalyzePage.tsx:281-297`; `src/trustforge/web.py:6611-6632`）。`submit_manual` 對 canonical coin/mode/question 加檔案鎖，並在 300 秒內重用 queued/running/completed manual job（`src/trustforge/analysis_flow.py:72-105,506-559`）。 | 不可稱為 client idempotency key、exactly-once 或「每次按重新分析必定 fresh run」。 | #957 → #958 → #940。 |
| direct `/api/analyze` 去重 | 另一條 direct endpoint 有 in-flight single-flight 與跨實例 lease；完成後不保留結果給後續新請求（`src/trustforge/web.py:4366-4384,4400-4412,4631-4671`）。 | 不可把它描述成 `AnalyzePage` 手動 job flow 的主契約。 | 保留既有 direct API contract；C04A/B 必須分開建模。 |
| 手動 job reconnect | 手動 job id 寫入 `sessionStorage`，以 coin/mode/question 為 key；reload 可接回 job（`frontend/src/pages/AnalyzePage.tsx:38-59,248-257,294-297`）。URL job 會驗證請求是否相符（`frontend/src/pages/AnalyzePage.tsx:28-36,230-233`）。 | 不可宣稱跨瀏覽器、跨裝置或 session 結束後仍可恢復。 | #957 → #958 → #940。 |
| claim identity | `BasisItem` 用 `evidence_idx: number[]`，UI anchor 是 `#evidence-{idx}`（`src/trustforge/schema.py:96-101`; `frontend/src/components/KeyBasisList.tsx:14-23`）。`Evidence.related_claim` 是自由字串，Evidence 本身沒有 `claim_id`（`src/trustforge/schema.py:42-53`）。部分 stance/insight 結構可帶選填 claim id，但不是全報告穩定 identity（`frontend/src/lib/types.ts:137-147,475-498`）。 | 不可宣稱所有 claim 已有穩定、可雙向解析的 ID。array index 不能跨重新排序／重新生成當永久識別。 | #959 → #960 → #941/#942/#949。 |
| Evidence contract | 官方最小欄位 `source/fetched_at/content_reference/related_claim` 已是 dataclass 必填；另有 source URL、kind、trust、flags、lineage 等（`src/trustforge/schema.py:42-53,88-93`）。公開 API 以 allowlist 暴露欄位並濾除 author（`src/trustforge/web.py:5243-5277`）。 | 不可把空 `source_url` 說成可點擊原始來源；不可把 `data_lineage=None` 說成缺陷，因非檔案 Evidence 合法無 lineage。 | #959 → #960 → #942 → #949（JSON/CSV parity）；CSV 尚不存在。 |
| execution log | Manual flow 的 `ExecutionLog.manifest()` 回 `agent/run_id/started_at/elapsed_sec/budget_sec/nodes/skill_revisions`，events 維持 JSONL 相容形狀（`src/trustforge/execlog.py:58-109,152-176`）。前端 `ExecutionManifest` 目前只有 nodes 等欄位，尚未型別化 `skill_revisions`（`frontend/src/lib/types.ts:544-570`）。 | 不可把 generic primitive 的 `steps` 當成 current public manual manifest，也不可宣稱 reviewer/public allowlist 已實作。 | #943（C07A security contract）→ #950（C07B timeline）；#943 需 harper + `/codex-review`。 |
| export | 報告頁可在瀏覽器下載 Markdown report、JSON evidence、JSON execution envelope（`frontend/src/components/ReportDownloads.tsx:23-35`; `frontend/src/lib/executionLogDownload.ts:3-12`）。Markdown 含結論、facts、inferences、evidence index、信心、限制與 could-flip（`frontend/src/lib/reportArtifacts.ts:15-39`）。 | 不可宣稱 server-side immutable artifact、CSV Evidence、JSONL raw log、Source-Config 或 submission pack 已存在。 | 不新增 server immutable artifact；#949 CSV parity + #944 hub + repository submission pack/Source-Config。 |

## 3. Intent 與題型的正確產品契約

### 3.1 現在可保證

1. 使用者可輸入任意自然語言市場問題；UI 不應要求先選官方三題型。
2. 現行 UI URL/report path 仍保留 `type`，但 manual registration 真正送的是
   `mode`；server 再把 fundamentals/catalyst 映射為 hypothesis，其餘既有
   focus 映射為 multi_source（`frontend/src/lib/endpoints.ts:120-130`;
   `src/trustforge/analysis_flow.py:68`）。
3. comparison 需要 `coin`、`coin2`、`q`，並使用不同 response shape
   （`frontend/src/lib/endpoints.ts:276-297`;
   `frontend/src/lib/types.ts:585-620`）。
4. 官方三種範例必須納入 release verification，但不得成為 composer、
   client validation 或 API input validation 的 whitelist／三選一阻擋；
   mixed/unknown 自然語言問題同屬 #953 驗證範圍。

### 3.2 尚未存在

- 沒有供現行 UI 呼叫的 `POST /api/intent-preview` 或同等 planning endpoint。
- 沒有 server-returned `detected_intents[]`、`combined`、工具計畫、資料需求、
  澄清問題或 preview confidence。
- 沒有證據顯示一個 query 可在 API 層同時帶多個 `QuestionType`。

目前已存在的 preview control-plane 基礎不是零：trusted AWS interval clock、
strict admission snapshot/action compiler、atomic admission executor、durable
admission gate、terminal reconcile 與 expired-lease recovery 已由
#967/#973/#983/#991/#992 及其 implementation PR 落地。這些模組負責
fail-closed cost/concurrency admission；`preview_terminal_reconcile.py` 也明示
本身沒有 provider integration。它們沒有改變上述 UI/API 缺口。

因此 #955 應先固定 contract，#956 完成剩餘 provider/API integration 後，
#939 UI 才可明標
「系統理解／可修改」；API 不可用
前端 keyword mapping 冒充 Hermes 的 server-side 判讀。preview 不可成為
正式分析的必要阻擋；preview 失敗時仍應容許提交原始自然語言問題，由正式
Hermes 流程處理。

## 4. Formal run identity 與重送語意

### 4.1 AnalyzePage 真正的 manual flow

主 UI 不是直接呼叫 `/api/analyze`。`AnalyzePage` 呼叫
`registerAnalysisQuestion`，POST `{coin, mode, question, locale}` 到
`/api/analysis-question`；handler 建立短生命週期 `AnalysisFlow()` 並呼叫
`submit_manual`，回 202 receipt `{question_id, job_id, state, origin}`
（`frontend/src/lib/endpoints.ts:104-130`;
`frontend/src/pages/AnalyzePage.tsx:281-297`;
`src/trustforge/web.py:6611-6632`）。

`submit_manual` 的現況語意：

1. coin 會 trim + uppercase，mode/question 會 trim；canonical key 是
   `coin\0mode\0question`（`src/trustforge/analysis_flow.py:518-524`）。
2. 同 key 取得以 SHA-256 命名的 filesystem `flock`，等待上限 90 秒
   （`src/trustforge/analysis_flow.py:72-105`）。
3. 鎖內查 SQLite：最近 300 秒、origin=manual、state 為
   queued/running/completed 的相同 coin/mode/question，會重用同一 job id
   （`src/trustforge/analysis_flow.py:525-530`）。
4. 相同 locale 直接回同一 job；locale 不同則把同一 job requeue、寫
   `job_relocalized` lineage、重跑 pipeline，不建立新 job
   （`src/trustforge/analysis_flow.py:531-559`）。
5. 沒命中才建立 snapshot 與新 manual job
   （`src/trustforge/analysis_flow.py:560-569`）。

測試已證明空白／大小寫 canonicalization 會重用 job 且只 collect 一次
（`tests/test_analysis_flow.py:157-176`）；兩個共用同一 DB path 的
`AnalysisFlow` instance 並發時，filesystem lock 讓它們只建立一個 job
（`tests/test_analysis_flow.py:179-227`）；不同 canonical key 可並行
（`tests/test_analysis_flow.py:230-261`）。locale 跨 web process 與 daemon
process 透過 DB/lineage 保留，且 5 分鐘內切換 locale 會 requeue 同一 job
（`tests/test_narrative_locale_n11.py:257-324`）。

### 4.2 跨 process、instance 與 DB 邊界

目前並發保證的實證邊界是：process/flow instances 共用同一 SQLite DB path，
且可見同一個 `.manual-locks` filesystem。`flock` 不是分散式 lock；若不同
service instance 使用各自本機 filesystem/SQLite，這個 manual dedup 不提供
跨主機原子性。現有測試只覆蓋同一 host、共享 path 的兩個 flow instances
（`tests/test_analysis_flow.py:179-227`），不得外推成多 instance distributed
exactly-once。

`question_id` 是 canonical coin/mode/question 的 deterministic hash，但
`job_id` 才是可輪詢的工作 receipt（`src/trustforge/analysis_flow.py:487-504`;
`frontend/src/lib/endpoints.ts:104-105,133-139`）。API 沒有接收 client
idempotency key，也沒有回傳 dedup-hit/fresh-created discriminator。

### 4.3 direct `/api/analyze` 是另一條 contract

direct endpoint 的 dedup 是成本保護用 in-flight coalescing，不是
`AnalyzePage` manual flow 的 run identity。它對同內容的同時請求共用結果，
leader 完成後下一個相同請求會 fresh compute
（`src/trustforge/web.py:4372-4384,4400-4412`）。跨實例 lease busy 表示
別處正在算，會要求 client 稍後重試，不會回 manual job receipt
（`src/trustforge/web.py:4595-4603`）。

### 4.4 C04 必須明確區分

- `run_id`：某次已接受執行的穩定身份。
- `5-minute content dedup`：目前 manual flow 的 coin/mode/question 時窗重用；
  completed 也會被重用。
- `client idempotency key`：目前不存在；應代表同一次提交意圖的 transport retry。
- `durable receipt`：目前有 DB job id，但沒有跨主機 exactly-once 保證或
  dedup disposition。
- `request fingerprint`：coin(s)、原始 question、有效執行模式與版本摘要。
- `fresh rerun`：目前 UI nonce 只會再 POST；若內容與 locale 相同且仍在 300 秒
  窗口，server 會回同一 queued/running/completed job，不保證新資料。

#957 必須設計 client idempotency key、fresh-rerun override 與跨 instance
backend；#958 實作後，#940 只能呈現 server 真實 disposition。
在 contract 落地以前，UI 只能使用現有 job id reconnect，不得生成前端 UUID
後宣稱 server-side exactly-once，也不得把現有「重新分析」宣稱為必定 fresh。

## 5. Claim ↔ Evidence identity

目前可用鏈路是：

`Report.key_basis[i]` → `evidence_idx[]` → `Evidence[evidence_idx]`

這能支援同一 response 內的「判斷跳到證據」，但不能支援：

- Evidence 回跳到唯一 claim（`related_claim` 是文字，不是 ID）。
- report 重排、Evidence grouping、重新分析後維持相同 anchor。
- export artifacts 間以永久 identity join。

#959 的最低相容方向應先由 API owner 決定；#960 實作完成後，#941/#942/#949
才能消費穩定 identity：

- 新增穩定 `claim_id` 到結論／basis claim；
- Evidence 使用 `related_claim_ids[]`（舊 `related_claim` 保留相容期）；
- UI 仍能讀舊 snapshot 的 `evidence_idx`；
- grouping 不改變原始 Evidence identity；
- 不以 array index 當跨 artifact 主鍵。

這段是 gap 方向，不是已核准 schema。

## 6. Evidence 與公開資料邊界

Evidence 公開 allowlist 已包含 schema/version、來源、取得時間、引用、關聯
主張、URL、種類、信任資料、flags、lineage 與 reputation mode；author 被明確
濾除（`src/trustforge/web.py:5250-5277`）。這是目前比 execution log 更成熟的
公開 contract。

UI 狀態必須誠實區分：

- 有有效 `source_url`：可開來源。
- 無有效 URL：顯示無有效來源連結；現有 UI 已如此處理
  （`frontend/src/components/EvidenceTable.tsx:15-18,40-46`）。
- 有 lineage：顯示檔案、coverage、SHA-256。
- 無 lineage：表示非檔案 Evidence 或舊資料，不自動判為錯誤。
- Evidence index 越界：目前 grouping 會 fail closed 不渲染該項
  （`frontend/src/components/EvidenceTable.tsx:107-119,163-166`）。

## 7. Execution log 與 public redaction

Manual analysis 的 current manifest 由 `src/trustforge/execlog.py` 組裝，固定
Hermes nodes 並附 `skill_revisions`（`src/trustforge/execlog.py:22-28,155-176`）。
`src/trustforge/execution_event_log.py` 是底層 generic event/run/step primitive，
提供 redaction 與 JSONL serializer；它的 generic `steps` manifest 不是對外
manual manifest（`src/trustforge/execution_event_log.py:61-69,102-127`）。
前端型別尚未宣告後端實際存在的 `skill_revisions`
（`frontend/src/lib/types.ts:556-570`），這是 #943/#950 消費 contract 時必須
處理的 compatibility gap。

目前 redaction 只在 event append 時，依 key 名稱識別 api key、auth、
credential、password、secret、token 等並遞迴替換
（`src/trustforge/execution_event_log.py:15-25,90-99,130-153`）。

#943（C07A）已採「public/reviewer allowlist、排除 raw question/connector
params」政策；安全審查仍需在 issue 內設計：

- public event 欄位 allowlist；
- params 的允許結構與大小上限；
- URL、路徑、address、header、exception message 的分類；
- 未知欄位 fail closed；
- 完整 audit artifact 的受控、非公開保存方式，以及是否需另開 issue；
- redaction regression fixtures。

在上述 contract 完成前，現有 JSON 下載只能標成「目前 run execution
envelope」，不能標成「已完成公開脫敏的官方 log」。#950（C07B）依賴
#940 + #943，兩者通過後才實作 timeline；不能反過來由 timeline UI 定義
安全邊界。

## 8. Export contract

### 8.1 四件交付物 UI contract

| 交付物 | Current source | Required fields / contents | Null / unavailable semantics | Error / recovery | Interaction / ARIA |
|---|---|---|---|---|---|
| Final Report | `AnalyzeData.report`；client 以 `reportMarkdown()` 產生 Markdown（`frontend/src/lib/types.ts:544-554`; `frontend/src/lib/reportArtifacts.ts:15-39`）。 | **Frontend runtime-required transport fields：** `coin`, `question_type`, `question`, `market_judgment`, `facts`, `inferences`, `key_basis`, raw `confidence`, `limits`, `could_flip`, `contrarian`, `generated_at`, `direction`, `cross_source_signal`（key 必須存在、值可 null）, `calibrated_confidence`；`decision_state` 是 legacy-tolerant required semantic：可缺／未知字串，渲染前 normalize。**Backend emitted：**另含 `schema_version`。`confidence_label` 是後端由 decision state + calibrated confidence 推導的方法結果，不是 transport field（`frontend/src/lib/validators.ts:63-74,235-255`; `src/trustforge/schema.py:104-120,147-196`）。**競賽畫面必呈現：**問題/資產、結論、事實與推論分層、關鍵依據及 Evidence 對照、校準信心的白話 label、反方證據、方向/跨源分歧、decision state、限制、could-flip、資料/生成時間。 | 明確 optional/legacy：`insights` 缺席→`[]`/未提供洞察；`hypothesis_ledger` 缺席或 null→非假設題或舊版未提供；`asset_context` 缺席/null→無資產脈絡；`risk_notices` 缺席→`[]`；`asset_intrinsic_assessment` 缺席→未執行 shadow；`evidence_groups` 缺席/null→Evidence flat mode；後端 `term_annotations` 缺席→無詞彙標註。`decision_state` 缺失/未知→normalize `normal`，但不可抹掉 limits。`limits=[]` 只代表未列限制，不代表零風險（`frontend/src/lib/types.ts:173-206`; `src/trustforge/schema.py:121-180`）。 | 下載失敗保留畫面資料並提供重試；validator-required 欄位畸形走 parse/error state，不生成空白報告；`schema_version` 缺席目前不會被 frontend guard 擋下，UI 不得自行聲稱版本；owner #941/#948。 | 報告標題用 page heading；結論變更不自行搶 focus；完成通知用 polite live region；Evidence refs 為可鍵盤操作連結並把 focus 移到目標 heading。 |
| Evidence List | `AnalyzeData.evidence[]`；client 現可下載 JSON，CSV parity 由 #949（`src/trustforge/schema.py:42-93`; `frontend/src/components/ReportDownloads.tsx:27-32`）。 | schema version、source、fetched_at、content_reference、related claim/未來 claim id、source URL、kind、trust、flags；檔案型資料另需 lineage/coverage/hash。 | 空陣列表示本 run 無可交付 Evidence，報告不得呈現 ready/高信心；空 URL 顯示「無有效來源連結」；`data_lineage=null` 對非檔案來源合法。 | 單筆 URL 無效不阻斷其他證據；export 失敗保留 Evidence 並可重試；identity/schema mismatch fail closed，owner #959/#960/#942/#949。 | table/card 必須有可讀 caption/heading；展開控制用原生 button/details 或 `aria-expanded`；跳轉後 focus 可見；錯誤摘要 polite，下載失敗 assertive。 |
| Execution Log | Manual `ExecutionLog.manifest()` + `events`；client 現下載 `{execution,events}` JSON（`src/trustforge/execlog.py:58-109,152-176`; `frontend/src/lib/executionLogDownload.ts:3-12`）。 | manifest: agent、run id、started/elapsed/budget、nodes、skill revisions；public events 只含 #943 allowlist，排除 raw question/connector params；受控 full audit 不公開。 | legacy snapshot 無 execution 時顯示「此版本無執行紀錄」，不得生成假 run id；event/skill revision 缺席標 unknown/not-recorded；partial run 顯示最後可信 stage。 | public projection 失敗不得 fallback 洩露 raw params；停用公開下載並指向重試/受控支援；owner #943/#950。 | timeline 使用有序清單與 heading；進度更新 polite 且節流，不逐 event 洗版；failed stage assertive 一次；展開/收合可鍵盤操作並保留 focus。 |
| Source / Config | Repository submission pack，不是 server artifact。來源包含版本化 source、`README.md`、`pyproject.toml`/`uv.lock`、`Dockerfile`、部署/執行說明與競賽文件；由 commit 綁定後打包。 | commit SHA、版本、目錄/入口、安裝與執行指令、AWS/Bedrock service/model/region 說明、必要 env **名稱**與 secret 注入方式（不含值）、依賴 lock、license/第三方素材、資料來源/設定、驗證步驟。 | 非 server runtime 產物；hub 只能連結/說明 repository pack。缺 commit、runbook、lock 或 secret-safe config 任一項即標「交件包未就緒」，不可用空檔代替。 | pack build/驗證失敗保留前一個已驗證 pack（若有）並顯示 commit；無已驗證 pack 時禁止宣稱完成，回到 repository release checklist 修復。 | hub 連結需寫明檔案類型/commit；外部或下載連結可鍵盤操作；狀態使用文字 + icon；pack 驗證結果 polite，阻斷交件錯誤 assertive 並把 focus 移到錯誤摘要。 |

四件交付物在 #944 hub 中必須各有獨立 readiness，不得以「分析 completed」
推導四件皆 ready。任一件 unavailable 時，其他可用交付物仍可檢視／下載，
但整體 competition package 必須標 incomplete。

### Current

- `{run_id}-report.md`
- `{run_id}-evidence.json`
- `{run_id}-execution-log.json`，形狀為 `{execution, events}`

以上均由瀏覽器當下 payload 建 Blob 下載，不是 server 保存的 immutable
artifact（`frontend/src/lib/reportArtifacts.ts:6-12`;
`frontend/src/lib/executionLogDownload.ts:3-12`）。

### Gap

- Evidence CSV。
- 原始 JSONL（現行前端下載是 JSON envelope）。
- source/config/執行說明。
- manifest（commit、schema、model/region、checksum）。
- 完整競賽包與離線驗證。
- artifact 生成失敗、缺 execution manifest、舊 snapshot 的明確 UI。

CEO 已決定不新增 server immutable artifact。#949 在 #960、#942 與 #937
export contract 完成後處理 Evidence JSON/CSV parity；#944（C08A）依賴
#941、#948、#942、#949、#950，只整合現有 client report/evidence/log 與
CSV 到交付中心。競賽其餘交件由 repository submission pack / Source-Config
提供。#951（C08B）依賴 #944 + #945，處理 presentation mode。

## 9. UI state matrix

| State | User message | Allowed action | ARIA / live behavior | Recovery / owner |
|---|---|---|---|---|
| initial | 「輸入任何市場問題，Hermes 會規劃分析。」三官方題型只作範例。 | 編輯問題、資產、語系；套用範例。 | composer 有可見 label/description；不送 live announcement。 | URL 無明確 request 時回此態（`AnalyzePage.tsx:119-130`）；#939。 |
| editing | 「尚未送出。」保留使用者原文。 | 繼續編輯、清除、要求 preview。 | validation 與欄位以 `aria-describedby` 關聯；不逐字 live。 | 換幣只在未手改問題時換預設文（`QueryConsole.tsx:40-61`）；#939。 |
| planning | 「Hermes 正在理解問題與規劃。」 | 取消 preview；不可誤送兩次。 | `role=status`, `aria-live=polite`, `aria-busy=true`；只公告開始/完成。 | Preview contract/implementation #955/#956；UI #939。 |
| preview unavailable | 「無法預覽，但仍可用原始問題分析。」 | 返回編輯、照原文繼續、重試 preview。 | polite 一次；不可用 assertive 冒充正式失敗。 | 不得以 client keyword 猜測補值；#955/#956/#939。 |
| confirming | 「請確認資產、問題、資料模式；尚未開始正式 run。」 | 返回編輯、確認送出。 | dialog 正確命名、focus trap、Escape 返回觸發鈕；摘要可由 screen reader 一次讀完。 | disposition/fresh 語意 #957/#958；UI #940。 |
| registering | 「正在登記分析工作。」 | 取消 client wait；不可重複提交。 | polite + `aria-busy=true`；busy retry 不逐次洗版。 | 保留輸入；server_busy 有限退避（`AnalyzePage.tsx:174-185,281-297`）；#940。 |
| queued | 「工作已排隊，離開頁面也可稍後接回。」 | 查看 job id、離頁、取消輪詢。 | polite 公告一次；queue position 更新需節流。 | Poll job；job id 保存 sessionStorage（`AnalyzePage.tsx:215-255`）；#940。 |
| running | 「Hermes 正在執行：{可信 stage}。」 | 查看進度、離頁；不可下載未完成交付物。 | stage 變更 polite；容器 `aria-busy=true`；不逐 event 公告。 | 持續成功回應即繼續 poll（`AnalyzePage.tsx:191-245`）；#940/#950。 |
| reconnecting | 「正在接回既有工作，未建立新 run。」 | 等待、返回 composer。 | polite 一次；focus 留在原位置。 | URL/session job mismatch 顯示可恢復錯誤（`AnalyzePage.tsx:230-255`）；#940。 |
| partial | 「分析完成但部分資料／階段不可用；以下結論受影響。」 | 看可用報告/Evidence/log、重試缺失部分或 fresh run。 | warning heading；polite，若結論可信度受重大影響則 assertive 一次。 | 不把 partial 當完整 ready；#941/#948/#943/#950。 |
| stale | 「這是既有結果，資料時間為 {time}；尚未取得 fresh run。」 | 查看舊結果、明確要求 fresh rerun。 | stale badge 含文字；polite；不只用顏色。 | #957/#958 定義 override/disposition；#940 呈現。 |
| completed | 「分析完成；請檢查四件交付物各自狀態。」 | 看 report、trace Evidence、log、下載 ready artifacts。 | polite 完成一次；focus 不自動跳；heading 可被快捷跳轉。 | Result 必須存在（`AnalyzePage.tsx:235-239`）；#941/#948/#942/#949/#943/#950/#944。 |
| failed | 「分析失敗：{白話原因}。輸入已保留。」 | 重試、返回編輯、查看 job id/支援資訊。 | `role=alert` 一次；focus 到錯誤摘要；技術細節可收合。 | job failed/API error（`AnalyzePage.tsx:239-244`）；#940。 |
| timeout | 「長時間未收到工作狀態；不代表 server 已取消。」 | 再連線、返回、複製 job id；不可直接重建 run。 | assertive 一次；後續 retry polite。 | 10 分鐘無成功 contact（`AnalyzePage.tsx:191-227`）；#940。 |
| export unavailable | 「{交付物} 尚不可下載：{原因}。」 | 使用其他 ready artifact、重試生成/投影、前往 repository pack。 | disabled control 必須有可見原因；錯誤 polite，交件阻斷 assertive。 | client report/evidence/log + #949 CSV + #944 hub；Source-Config 走 repository pack。 |
| unauthorized | 「完整 audit artifact 不公開；目前僅可看 reviewer/public view。」 | 返回 public timeline；有權使用者走受控入口。 | `role=alert` 僅在實際拒絕時；focus 到安全返回動作；不洩漏資源細節。 | #943 allowlist/authorization policy；是否另開 controlled-audit issue 由安全審查決定。 |

## 10. Viewport baseline contract

這是 #937 提供給後續 issues 的驗收基準，不代表現況已通過。

| Viewport | 核心驗收 |
|---|---|
| 320×568 | composer、主要 CTA、狀態、報告首層不得水平溢出；Evidence 改為可讀 reflow，不以整頁 table 橫捲作唯一方案。 |
| 390×844 | 手機主要基準；首屏可辨識自然語言入口與主要 CTA；44×44 CSS px 觸控目標；無巢狀捲動陷阱。 |
| 768×1024 | 平板單欄／受控雙欄；導覽、report、Evidence、timeline 不互相遮擋。 |
| 1024×768 | 小型投影／筆電；核心 judgment、confidence、basis、limits 在合理首屏層級。 |
| 1440×900 | 競賽投影主要基準；30 秒內能辨識結論、理由、信心、限制與 Evidence 入口。 |

所有 viewport 共通：

- 無頁面級非預期水平溢出。
- 鍵盤 focus 順序與視覺順序一致，focus 不被 sticky layer 遮住。
- 200% zoom 仍可完成 composer → run → report → evidence → export。
- reduced motion 不影響狀態理解。
- loading/error/empty/partial 不只靠顏色或動畫。
- 中文、英文與長 URL／hash／claim 文本均需驗證。

最終 gate 已拆成 #946（C10A automated contract/viewport/a11y gate）、
#953（C10B official + mixed/unknown E2E）與 #954（C10C final manual
certification）。官方案例與 mixed/unknown 自動 E2E 在 #953，不得只留到
#954；所有上游 UI issue 仍對受影響 viewport 負責。

## 11. Dependency order

1. #937 固定 truth/baseline；只有 CEO 可宣告 Gate A。
2. #938（C02A App Shell）→ #947（C02B mobile navigation）。
3. #955（C03A contract，OPEN）→ #956（control-plane/provider/API
   integration，OPEN）→ #939（UI，OPEN）；#939 同時依賴 #938。#956 的
   #967/#973/#983/#991/#992 基礎切片已 CLOSED 且 implementation PR 已
   MERGED，但不得據此把 #955/#956/#939 標成完成。
4. #957（C04A contract）→ #958（implementation）→ #940（UI）；
   #940 同時依賴 #939。
5. #959（claim contract）→ #960（implementation）→ #941（C05A shared
   report summary + analysis adapter）；#941 同時依賴 #937 + #939。
   #948（C05B comparison/combined adapters）依賴 #941。
6. #960 → #942（C06A claim ↔ Evidence trace UI）→ #949（C06B Evidence
   JSON/CSV export parity）；#949 同時依賴 #937 export contract。
7. #943（C07A public redaction/security contract）→ #950（C07B reviewer
   timeline）；#950 同時依賴 #940。#943 安全敏感，需 harper +
   `/codex-review`，不得稱 #943 為 timeline issue。
8. #941/#948/#942/#949/#950 → #944（C08A delivery hub）；#944 只整合
   client artifacts，不新增 server immutable artifact。#944 + #945 →
   #951（C08B presentation）。Source-Config 由 repository submission pack
   交付。
9. #945（C09A typography/tokens）→ #952（C09B copy/i18n）；依賴 #938、
   #941 的結構決策。
10. #946（C10A automated contract/viewport/a11y gate）→ #953（C10B
    official + mixed/unknown E2E）→ #954（C10C final manual certification）。

任何 issue 若估算超過 12 小時，必須先拆分，不能擴張原 scope。

文件的正確依賴為 `#959 → #960 → #941/#942/#949`；GitHub issue dependency
若尚未一致，CEO 需另行同步。本文件不修改 GitHub issue。

## 12. Gate A unresolved questions

方向已由 CEO 決定；以下只是在指定 contract/security issue 內仍需設計的項目：

1. #955：preview endpoint/registration response 的邊界、mixed/combined intent
   的持久化程度與 fallback。
2. #957：300 秒 content dedup 的相容策略、fresh-rerun override、client key
   生成與保存期限、跨 instance backend、receipt/disposition shape。
3. #959：claim identity canonical owner、舊 snapshot migration 與 schema
   versioning。
4. #943：public allowlist 的逐欄定義、未知欄位 fail closed，以及受控完整
   audit artifact 是否需另開 issue。

上述 contract/implementation 未落地前，下游 UI 不可把方向決策冒充成已完成能力。
截至本基準，#955–#960 與 #939–#960 的本文件主要下游 issues 仍為 OPEN；
本文件只記錄已查證的子切片狀態，不替 GitHub issue 作結案判定。

## 13. #937 acceptance checklist

- [x] 逐項稽核 intent preview、任意/combined intent、manual formal flow、
  direct analyze dedup、claim identity、Evidence、manual execution log/public
  redaction 與 exports，且每項引用 current code/type/test。
- [x] 明確區分 manual `submit_manual` 的 300 秒 content dedup、client
  idempotency key、durable receipt、fresh rerun，以及 direct `/api/analyze`
  single-flight。
- [x] 記錄跨 process/instance/DB lock 的實證邊界，不外推跨主機 exactly-once。
- [x] 四件競賽交付物各自定義 current source、必填內容、null/unavailable、
  error/recovery 與 ARIA/interaction；Source/Config 明定為 repository
  submission pack，非 server artifact。
- [x] 狀態矩陣涵蓋 initial、editing、planning、preview unavailable、
  confirming、registering、queued、running、reconnecting、partial、stale、
  completed、failed、timeout、export unavailable、unauthorized，並逐列定義
  user message、allowed action、ARIA/live 與 recovery/owner。
- [x] Viewport baseline 涵蓋 320、390、768、1024、1440，並包含 zoom、
  keyboard、reduced motion、長字串與非顏色狀態表達。
- [x] 官方三題型只作 release cases；自由問題與 mixed/unknown 同列 #953 E2E。
- [x] CEO decisions 與 #955–#960、#939–#954 owner/dependency 已寫入；需同步的
  GitHub dependency 已明示。
- [x] 本文件未宣告 Gate A 通過；Gate A disposition 只由 CEO 決定。
