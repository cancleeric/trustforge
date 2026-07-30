# Issue #955 — Hermes Intent Planning API Contract

日期：2026-07-29
Owner：gray（CPO）
審查：待本 PR 的 CEO、harper（CISO）與 adversarial reviewer 新審
狀態：依目前 `origin/develop` 與 tracker 重整中；舊 commit 的 disposition
不沿用為本次變更的 commit-bound 核准

## 1. 目的與非目標

本合約定義一個**不建立正式分析工作**的 Hermes planning preview：

```text
使用者輸入任意自然語言
  → POST /api/analysis-plan
  → Hermes/AWS Bedrock 僅回傳規劃建議
  → 使用者自行確認或修正
  → 另一次 POST /api/analysis-question 才是正式送出
```

核心產品規則：

- 任意中文、英文、混合語言、單一意圖、多重意圖與無法判定的問題，都送
  Hermes planning；不得先以固定題型白名單擋下。
- 既有三種官方題型只能作 fixture、示範與 release case，不是輸入限制，也
  不是 planner 能力邊界。
- planning output 是不具權威性的建議，不是 formal execution authorization。
- preview 的任何不確定、clarification、429、503 或 504 都不能阻止使用者以
  仍符合正式驗證的原始問題送出正式分析。
- v1 只由明確按鈕觸發；禁止輸入 debounce、逐字自動呼叫。
- v1 不快取。

非目標：本 issue 不實作 endpoint、UI、formal idempotency、Hermes job、
connector 選擇或執行，也不新增第四種 `QuestionType`。

> **Release-blocking truth：**open-intent preview 與 open-intent formal
> execution 是兩個不同能力。preview 通過不代表 formal path 已能執行任意
> intent；競賽 UI 在 formal bridge 實作、驗證完成前，不得宣稱「任何題型都能
> 開始 Hermes agent 分析」。

## 2. 現況稽核與目標差異

### 2.1 正式輸入邊界

真實 formal manual path 是
`POST /api/analysis-question` → `_handle_api_analysis_question()` →
`AnalysisFlow.submit_manual()` → `register_question()`。`register_question()`
先對 question 執行 `strip()`，再要求非空且 `len(question) <= 1000`
（`src/trustforge/web.py:6609-6640`;
`src/trustforge/analysis_flow.py:471-478`）。

同一個 `register_question()` 還會要求 `coin in COIN_POOL` 且
`mode in QUESTION_TYPES`，不符合即丟出 `unsupported coin or mode`
（`src/trustforge/analysis_flow.py:471-476`）。目前 formal request 仍由 client
傳入 `coin` 與固定 `mode`，沒有把 preview 的 open intent plan 路由成 Hermes
agent 可執行的任意題型。因此：

- #964 只擁有 preview endpoint 與 typed client，不能偷偷改 formal routing。
- #939 只能顯示 open-intent preview；在 formal bridge 完成前，不能把既有固定
  `coin/mode` registration 包裝成任意題型正式執行。
- CEO 已建立真實 owner：#965（formal open-intent routing contract，4–6h）
  → #966（formal open-intent routing implementation，8–12h）。兩者明確定義
  raw question/asset resolution、相容的 legacy coin/mode adapter、Hermes
  agent dispatch、receipt/job contract、錯誤/成本/安全邊界與 migration；
  #964 不擁有此能力。

因此 preview 必須採相同的 canonical validation：

1. JSON string；
2. Unicode whitespace trim；
3. trim 後 1..1000 Unicode code points。

preview 不得接受 formal 隨後必定拒絕的 4000 字問題。16 KiB body limit 是
transport 防護，不放寬 question limit。

現有 formal typed client/API adapter 的 locale 是 `zh-Hant|en`，非法值由
server `normalize_locale` fallback；preview 新合約刻意在 public request 使用
UI locale `zh-TW|en` 並嚴格拒絕其他值。#964 的 typed adapter 必須把 preview
使用的 `zh-TW` 與正式送出使用的 `zh-Hant` 作顯式轉換，不能直接共用 type 或
依賴 formal 的 fallback。兩條 route 不應互相假設 locale wire value 相同
（`frontend/src/lib/endpoints.ts:106-131`; `src/trustforge/web.py:6621-6629`）。

### 2.2 Origin、proxy 與 cache

- API 已有顯式 Origin allowlist，會忽略 wildcard；未列入的 Origin 不取得
  CORS headers（`src/trustforge/web.py:149-177`）。preview 的目標比共用
  行為更嚴：production 必須有 exact HTTPS release allowlist，缺失即 startup
  或 endpoint fail closed。
- `_resolve_client_ip()` 在 `TRUST_PROXY` 開啟時信任 `X-Real-IP`，再退回
  `X-Forwarded-For`；函式本身不驗證 ingress，安全性仰賴 Python 強制 loopback
  bind 與 nginx 覆寫 headers（`src/trustforge/web.py:527-551`;
  `deploy/README.md:432-437`）。preview 只能在此 trusted-ingress topology
  已被部署檢查證明時使用 forwarded identity；直連 client headers 一律不是
  identity evidence。
- 共用 `_send()` 目前只保證 admin route 的 `Cache-Control: no-store`；
  preview 必須在所有 success/error response 額外明確送
  `Cache-Control: private, no-store`，且不送 ETag
  （`src/trustforge/web.py:8189-8222`）。

### 2.3 rate/cost backend

現有 `rate_limit_store` 使用 DynamoDB 原子 fixed-window increment，但 backend
錯誤的既有契約是由 caller fallback 至 process-local
（`src/trustforge/rate_limit_store.py:1-35,38-49`）。
現有 `budget_counter`/`budget_guard.try_reserve_request_budget()` 亦在共享
backend 錯誤時 fallback 至 process-local
（`src/trustforge/budget_counter.py:34-36,62-67`;
`src/trustforge/budget_guard.py:555-595`）。

該 fallback 對付費公開 preview 不足：多 instance 會各自放行。preview
專屬 durable admission foundation 已拆分落地，而不是由單一 #967 擁有整套
store：

- #967／PR #977：fail-closed trusted AWS interval clock
  （`preview_trusted_clock.py`）；
- #972／PR #982：versioned DynamoDB schema 與 circuit CAS primitives
  （`preview_admission_store.py`）；
- #973：strict admission snapshot/action compiler
  （`preview_admission_compiler.py`）；
- #983：atomic admission executor
  （`preview_admission_executor.py`）；
- #991：atomic terminal reconcile 與 concurrency release
  （`preview_terminal_reconcile.py`）；
- #992：expired lease recovery、crash recovery 與 ambiguous latch resolution
  （`preview_lease_recovery.py`，並接入 durable admission gate/executor）。

上述 issues 均已 closed，對應元件與測試已在目前基底；這代表 durable store
foundation 已完成，不代表 #956 的完整 control-plane orchestration、#964
endpoint/provider adapter 或 #939 UI 已完成。#956 應組裝並驗證這些既有元件；
共享 limiter、budget、concurrency、clock、circuit、reconcile/recovery 或 price
policy 任一不可用時直接 503，**不得**呼叫現有 process-local fallback。

## 3. HTTP contract

### 3.1 Request

`POST /api/analysis-plan`

必要 headers：

```http
Content-Type: application/json
Origin: https://<exact-release-origin>
```

Request body 上限 16 KiB；JSON object 必須 `additionalProperties: false`：

```yaml
type: object
additionalProperties: false
required: [question, locale]
properties:
  question:
    type: string
    minLength: 1
    maxLength: 1000
    description: trim 後 1..1000 Unicode code points；不得全為 whitespace
  locale:
    type: string
    enum: [zh-TW, en]
  asset_hints:
    type: array
    maxItems: 8
    uniqueItems: true
    items:
      type: string
      minLength: 1
      maxLength: 16
      pattern: '^[A-Z0-9][A-Z0-9._:-]{0,15}$'
  client_request_id:
    type: string
    format: uuid
    description: optional UUIDv4；僅存於單次 request lifecycle 記憶體，不提供 idempotency 保證
```

asset symbol grammar 是 uppercase ASCII 字母或數字起首，其後僅 uppercase
ASCII 字母/數字、`.`、`_`、`:`、`-`。`asset_hints` 在 wire 上就必須是
canonical uppercase、無前後 whitespace、case-sensitive unique；server 不做
trim、uppercase 或 dedup 修正，任何非 canonical/重複值直接 400。任何 unknown key、duplicate JSON key、非 UUIDv4、錯型、
超限、非法 Unicode scalar/encoding 或 trailing JSON 均回固定 400，且不呼叫
provider。

`client_request_id` 在 response 完成或中止後立即丟棄；禁止寫入 persistence、
logs、traces/APM、metrics/metadata、cache、shared limiter/budget/circuit
backend 或 7 日 allowlist metadata。

### 3.2 Success union

HTTP 200 的**完整回應**是下列 strict envelope；envelope、`data` union 的每個
variant 與每層 nested object 都是 `additionalProperties: false`。不得回傳
`original_question`、raw prompt、provider response、model id 或 region。

```yaml
type: object
additionalProperties: false
required: [ok, data]
properties:
  ok:
    const: true
  data:
    oneOf:
      - $ref: '#/components/schemas/AnalysisPlanReady'
      - $ref: '#/components/schemas/AnalysisPlanNeedsClarification'
    discriminator:
      propertyName: outcome
```

等價的 typed shape：

```ts
type PlanSuccessEnvelope = {
  ok: true
  data: AnalysisPlanReady | AnalysisPlanNeedsClarification
}
```

所有 model-derived string 只能以 escaped plain text 渲染。禁止
`innerHTML`、Markdown/HTML interpretation、由字串生成 URL/navigation/action
或任何 executable behavior；hostile markup 與 bidi controls 不得改變
DOM/action 語意。

```ts
type PublicPlannerProvenance = {
  planner: 'hermes'
  provider: 'aws-bedrock'
  policy_version: string // 1..32, /^[A-Za-z0-9._-]+$/
}

type Intent = {
  label: string      // open label, 1..64
  rationale: string  // 1..240
}

type PlannerConfidence = {
  level: 'low' | 'medium' | 'high'
  rationale: string // 1..160
}

type Clarification = {
  id: string         // 1..32, /^[A-Za-z0-9._-]+$/
  question: string   // 1..240
  options: string[]  // 0..6; each 1..80
}

type AnalysisPlanReady = {
  outcome: 'ready'
  detected_assets: string[] // 0..8; same symbol grammar
  intent_shape: 'single' | 'multiple' | 'unknown'
  intents: Intent[]          // 0..8; unknown may be []
  source_classes: string[]   // 0..12; each 1..48 from server allowlist
  strategy_summary: string   // 1..600
  clarifications: Clarification[] // 0..3
  warnings: string[]         // 0..8; each 1..160
  confidence: PlannerConfidence
  provenance: PublicPlannerProvenance
}

type AnalysisPlanNeedsClarification = {
  outcome: 'needs_clarification'
  detected_assets: string[] // 0..8
  intent_shape: 'single' | 'multiple' | 'unknown'
  intents: Intent[]         // 0..8
  source_classes: string[]  // 0..12
  strategy_summary: string  // 1..600
  clarifications: Clarification[] // 1..3
  warnings: string[]        // 0..8
  confidence: PlannerConfidence
  provenance: PublicPlannerProvenance
}
```

`confidence` 只是 model 對「本次意圖/規劃理解是否充分」的 self-assessment，
不是校準機率、事實正確率、分析結論信心或 truth score；它同樣只作 escaped
plain text 呈現，永遠不是 formal execution authorization。

`source_classes` 是 server policy allowlist 的公開分類，不是 connector 名稱或
執行授權。provider 若多給欄位、輸出非法 discriminator、超限、空必填字串或
不符合 schema，server 不修補、不二次詢問模型，回 503。

### 3.3 Envelope 與 safe errors

沿用 API envelope 概念，但 preview error code 只有以下固定集合：

```ts
type PlanErrorEnvelope = {
  ok: false
  error: {
    code:
      | 'invalid_plan_request'       // 400
      | 'plan_rate_limited'          // 429
      | 'plan_temporarily_unavailable' // 503
      | 'plan_timeout'               // 504
    message: string
    retryable: boolean
  }
}
```

固定 mapping：

| HTTP | code | retryable | 安全訊息 |
|---|---|---:|---|
| 400 | `invalid_plan_request` | false | 請檢查問題、語系與資產提示格式。 |
| 429 | `plan_rate_limited` | true | 規劃請求過於頻繁。你可以返回編輯，或稍後再試。 |
| 503 | `plan_temporarily_unavailable` | true | Hermes 規劃暫時不可用。你可以返回編輯。 |
| 504 | `plan_timeout` | true | Hermes 規劃逾時。你可以返回編輯。 |

錯誤 envelope 不承諾 formal 支援目前輸入。UI 只有在當下 formal contract
確實支援該 input 時才可提供「直接正式送出」：#966 前不得暗示任意題型可
formal；#966 完成且 raw question 通過新 formal validation 後才可提供。preview
失敗本身仍不能使一個**獨立符合當下 formal contract**的 input 失效。

不得在 response 或 headers 暴露 provider exception、AWS request id、model、
region、budget 數值、identity key、circuit 狀態細節、prompt 或 raw output。
不新增 502；provider/schema/backend/circuit/price-policy failures 統一安全 503。
完整 provider payload（固定 system policy、canonical request 與實際送入的
schema/config）須在呼叫前以 exact allowed-model tokenizer 計數。若超過
2,048 input tokens，禁止截斷、禁止 provider call，固定回 503
`plan_temporarily_unavailable`。raw request 本身仍是 formal-valid，故不是
400，亦不得阻擋 formal submit。

所有回應：

```http
Cache-Control: private, no-store
Content-Type: application/json
Vary: Origin
```

不得產生 ETag，service worker/CDN 必須 bypass/no-store。

## 4. Planner authority 與執行隔離

Planner executor 只接收合約中的 canonical question、locale、asset hints 與
固定 system policy。它：

- 無 tool call；
- 無 connector、任意 network、filesystem、history、secret、analysis DB、
  formal job/queue、receipt、dedup lock 或 formal quota 權限；
- 不讀既有 conversation/history；
- 不接受 client 指定 model、prompt、source、tool、budget 或 region；
- output 只能通過 server strict schema projector，不能直接驅動 formal run；
- attempts 恆為 1，無 retry、repair pass、fallback model 或第二次 completion。

preview 使用獨立 executor pool、limiter/budget/circuit namespace與 global
semaphore；不能佔用 formal worker pool。即使 preview 飽和或 backend 故障，
formal `/api/analysis-question` 仍可獨立驗證與排隊。

## 5. 精確成本、容量與 circuit policy

| Control | v1 value |
|---|---:|
| provider input cap | 2,048 tokens |
| provider output cap | 512 tokens |
| provider timeout | 5 秒 |
| endpoint total deadline | 6 秒 |
| total attempts | 1 |
| per identity concurrency | 1 |
| per identity rate | 3/UTC fixed 60-second bucket；20/UTC calendar day |
| global preview concurrency | 4 |
| global token ceiling | 8,000/UTC fixed 60-second bucket；51,200/UTC calendar day |
| global USD ceiling | USD 0.05/UTC fixed 60-second bucket；USD 0.50/UTC calendar day |
| circuit | 60 秒內 5 次 provider failure → open 120 秒 |
| metadata retention | 最長 7 日 |

只有 TCP peer 精確命中 production trusted-proxy allowlist，才可接受 ingress
覆寫的**單一** forwarded address；非 allowlist peer 的 `X-Real-IP`/
`X-Forwarded-For` 一律忽略且 production endpoint fail closed。本機明確
development mode 只使用 TCP peer。production enablement 必須由 startup/
runtime assertion 與 integration test 證明 bind topology、exact proxy
allowlist、ingress overwrite 三者成立。production 若 exact origin、trusted ingress、
共享 backend、preview model price policy 任一缺失/過期，endpoint fail closed。

該單一 address 必須由 IP parser 接受；含逗號清單、IPv6 zone id、空值或
malformed address 一律拒絕。identity 以 RFC canonical binary address 表示，
IPv4-mapped IPv6 必須 collapse 成 IPv4。禁止保存 raw IP；shared key 使用
purpose-separated keyed digest。

key rotation 採 exact dual-write protocol。current/previous overlap 至少為
24 小時 + 90 秒 clock-staleness bound，且絕不得在所有 rotation 前已存在的
UTC daily/minute buckets 與 concurrency leases 到期前結束。overlap 期間每次
admission `TransactWrite` 必須同時 condition-check 並 increment/reserve
current 與 previous quota identity records；任一 resulting request count 超過
3/60s 或 20/day，或任一 resulting concurrency count 超過 1，整個 transaction
拒絕且兩邊皆零變更。reservation 必須保存兩個 key versions；reconcile、
release 與 TTL recovery 都須對兩份 records 以 reservation ID idempotent 更新。
observability digest 仍使用獨立 purpose separation，不能與 quota identity key
互換。

request/token/USD 的短窗是 UTC epoch 對齊的 fixed 60-second bucket，長窗是
UTC calendar-day bucket。固定 60 秒 bucket 在邊界可出現接近兩倍短窗額度的
瞬時 burst，這是明示接受的語意；UTC day hard cap 仍不可跨同一 calendar day
超額。bucket time 不可只信 app clock：production 必須至少每 30 秒以 trusted
AWS HTTPS response `Date` source refresh clock health。每次 sample 都記錄
monotonic send/receive，並把 network RTT 與 HTTP `Date` 秒級 precision 納入，
建構保守 trusted UTC interval，而非假設單一精確 timestamp。sample 完成後，
admission interval 只依 monotonic elapsed 向前平移，永遠不再由 app wall clock
推進。

只有 trusted interval 完全落在唯一一個 UTC 60-second bucket 且唯一一個 UTC
calendar-day bucket 時才可 admission。interval 只要跨越**任何** 60 秒或
calendar-day boundary，一律 fail closed、零 admission，直到 fresh sample/
monotonic advancement 使整個 interval 完全落在各自唯一 bucket；沒有使用舊
bucket 或其他 boundary 例外。clock step/backward、monotonic reset、process restart 尚無 fresh sample、
最後成功 refresh stale >90 秒，或 sample 相對 app wall clock 的 absolute skew
>2 秒，全部 fail closed，且不 admission/provider call；wall-clock skew 僅作
健康拒絕訊號，不作 admission time authority。

400 與在
origin/proxy/schema/rate/budget/circuit admission 前被拒的請求不計 request
quota；成功完成 transactional admission 的請求立即消耗 identity request
count，即使之後 provider/schema/timeout 失敗也不退回。rate/count denial 固定
429 `plan_rate_limited`。token/USD 依 reserve/reconcile 規則處理，concurrency
lease 則在所有 terminal path 釋放。

內部 policy artifact 必須 versioned，並以不可分割版本綁定：

- exact allowed model ID（只在受控內部，public response 仍隱藏）；
- exact tokenizer package name/version 與 vocabulary hash；
- price policy；
- source-class allowlist version `analysis-plan-source-classes-v1`。

production 若 model ID、tokenizer package/version/vocab hash 或 allowlist
artifact 任一 mismatch/unavailable 即 fail closed。v1 source classes 僅可為：

```text
market_price, derivatives, on_chain, news, social, regulatory,
macroeconomic, project_primary, exchange, security_incident,
governance, research
```

這些是穩定的資料需求分類，不是 connector 名稱、URL、tool selection 或執行
權限；provider 產生其他值即 strict schema failure。

USD policy 必須 configuration-versioned，依被允許的確切 model 計價。完整
provider payload 先以該 allowed model 的精確 tokenizer 計數；不可用近似字數
或其他 model tokenizer。通過後，以實際 input tokens + 512 output worst-case
同時原子 reserve：

- identity UTC 60-second/day bucket count；
- identity/global concurrency；
- UTC 60-second/day token；
- UTC 60-second/day USD。

單一 atomic admission transaction 必須同時包含 circuit closed condition
（或到期後唯一 half-open lease）、request count、identity/global concurrency
leases、token reserve 與 USD reserve。全部成功才成立；任何拒絕或部分失敗都
不得留下 state/quota/lease，也不得靠 best-effort compensating rollback。
底層無法保證全有或全無即 fail closed，不呼叫 provider。

concurrency reservation 必須是不可偽造 owner token + 有界 TTL 的 lease；
success、schema/provider error、timeout、client abort 都在 `finally` 做
owner-checked release，process crash 由 TTL 回收。token/USD reconcile 依下列
固定矩陣；每次 reservation 有唯一 `reservation_id`，所有 reconcile/release
都須以該 ID idempotent：

| Disposition | Token / USD reconcile |
|---|---|
| Known success + signed/validated trustworthy provider usage | actual usage |
| Known provider error + trustworthy usage | actual usage |
| Strict output/schema failure | trustworthy usage 存在則 actual；否則 full reserve |
| Timeout、client abort、unknown provider disposition | full reserve |
| Provider invocation 前的本地 failure | full rollback |

每一列都在 `finally` owner-check release concurrency lease；process crash 由 TTL
回收。reconcile backend failure 鎖閉整個 preview，直到 authority 恢復，且
不得改走 process-local。

circuit state 跨 instance 且原子。global circuit 只計與 prompt/output
內容無關的 provider transport/connect failure、provider HTTP
5xx/throttle/unavailable 與 5 秒 timeout。provider response parse/strict output
schema failure仍回 503，且已 admitted 的 request/token/USD accounting 不退回，
但**不得**增加或打開 global circuit；hostile prompt 不能藉由誘發壞 output
使所有使用者被開路。client abort、request 400、rate/budget/concurrency
denial、origin/proxy/price/token-policy failure與 shared-backend failure亦不計。
provider invocation 前須 atomic open check；60 秒內第 5 次 failure 開路
120 秒。到期後只允許一個 owner+TTL 的跨 instance half-open lease，其餘仍
503；probe 成功即原子清空 window 並 close，失敗即 reopen 120 秒，holder
crash 由 TTL 回收，不得形成多個 probes。

## 6. Privacy、logging 與 threat model

raw question、clarification answers、asset hints、完整 prompt、provider raw
response 與 exception 是敏感資料，禁止進入：

- application/access/error logs；
- analytics、traces、APM spans/events；
- metrics labels；
- cache、service worker、CDN、ETag；
- persistent debug payload。

允許最多保存 7 日的 metadata allowlist：

```text
timestamp bucket、policy_version、outcome、HTTP status、safe error code、
latency bucket、input/output token count、estimated/actual USD、
rate/budget/circuit disposition、trusted identity 的不可逆 keyed digest
```

不得保存原始 IP；digest key 必須受控輪替且不可輸出。provider request id、
model/region 可進受控 competition execution evidence，但不進 public provenance。

主要威脅與控制：

| Threat | Required control |
|---|---|
| prompt injection 要求工具、secret、connector 或建立 job | no-tool/no-authority executor；strict projector；output 非 authorization |
| schema smuggling / unknown provider fields | 每層 strict schema；unknown/duplicate fields fail closed |
| Unicode、超長、壓縮/JSON abuse | 16 KiB raw body limit；UTF-8/JSON/string/array bounds；無 provider call on 400 |
| XFF/X-Real-IP spoof | trusted ingress overwrite + loopback topology；直連 header 不採信 |
| 多 instance race 超額 | shared atomic reserve/reconcile；backend failure 503，無 local fallback |
| provider hang/retry 放大 | 5s provider、6s total、attempts=1、無 retry/repair |
| preview 餓死 formal | executor/semaphore/budget/queue 全隔離；formal starvation regression |
| 敏感字串被觀測工具擷取 | zero-capture instrumentation test，log/trace/APM/metric sink 掃描 |
| browser/CDN 留存 | private/no-store、no ETag、SW/CDN bypass |
| cross-origin/CSRF | same-origin JSON POST；exact HTTPS Origin allowlist；沿用並強化現有 Origin policy |

## 7. UI state 與 fallback contract

| State | Trigger | UI requirement | Formal action |
|---|---|---|---|
| editing | 尚未 preview | 保留原文，不自行分類 | 可直接 formal submit（須通過 formal validation） |
| planning | explicit preview click | CTA 防重送、可取消 client wait、polite busy | 不建立 job |
| ready | `outcome=ready` | 顯示 assets、open intents、sources、strategy、warnings | 使用者確認/修正後另送 formal |
| needs clarification | `outcome=needs_clarification` | 顯示最多 3 題作 advisory；v1 沒有 structured answer request field。使用者只能編輯 raw question/asset hints，再明確觸發一次全新的 preview，或略過 preview | 可略過 clarification 直接 formal |
| invalid | 400 | 關聯欄位錯誤；保留原文 | 同一 invalid raw input 不得繞過 formal 驗證 |
| limited | 429 | 顯示返回編輯／稍後重試；僅當 current formal contract 支援此 input 才顯示 formal CTA | preview 失敗不使 independently valid formal 失效 |
| unavailable | 503 | 不臆測 intent；保留原文；#966 前不暗示 arbitrary formal | current formal 支援時才可送出 |
| timeout | 504/client abort | 不聲稱 provider 未執行；不得 client auto-retry；formal CTA 同上 | current formal 支援時才可送出 |

preview 結果在 question、locale 或 asset hints 改變後即標 stale，不可暗示仍代表
目前輸入。clarification answer 不得藏在 client state 後宣稱已被 Hermes
採納，也不得擴充 request body；只有使用者實際改寫 `question`/`asset_hints`
並明確再次 preview，planner 才能看見新資訊。`client_request_id` 只供觀測
關聯；此關聯嚴格限單一 request 的 memory-only control flow，不得進入任何
persistence/log/trace/metadata/shared backend，也不得讓 UI 宣稱 exactly-once。

## 8. OpenAPI 與 typed-client proposal

#964 必須把第 3 節轉為機器可驗證 OpenAPI components，並由同一 schema 產生或
手寫等價的 TypeScript guard。frontend guard 必須：

- 精確比對 union discriminator；
- 拒絕額外欄位，而非只檢查少數 required keys；
- 對每個 nested string/array 重驗 bounds 與 allowlist；
- 不 fallback 成 client keyword classification；
- abort/timeout 不自動 retry；
- 不把 raw error body寫入 console。

建議 client surface：

```ts
export type AnalysisPlanRequest = { /* §3.1 exact shape */ }
export type AnalysisPlan = AnalysisPlanReady | AnalysisPlanNeedsClarification

export function previewAnalysisPlan(
  request: AnalysisPlanRequest,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AnalysisPlan>>
```

`REGISTER_TIMEOUT_MS` 與 formal receipt 型別不得重用來暗示相同 side effects；
preview client 應有 6 秒 contract 對應的獨立 timeout。

## 9. Issue 切分與依賴

```text
#955 contract refresh（本 PR 待新審）
  └─ durable admission foundation（已落地）
       ├─ #967 / PR #977 trusted interval clock
       ├─ #972 / PR #982 DynamoDB schema + circuit CAS
       ├─ #973 admission snapshot/action compiler
       ├─ #983 atomic admission executor
       ├─ #991 terminal reconcile + concurrency release
       └─ #992 lease/crash/ambiguous-latch recovery
         └─ #956 Hermes preview security/cost control plane (open, 8–12h)
            └─ #964 strict endpoint + typed client (8–12h)
                 └─ #939 arbitrary-natural-language preview/composer UI (8–12h)

#965 formal open-intent routing contract (4–6h)
  └─ #966 formal open-intent routing implementation (8–12h)
       ├─ #939 arbitrary-formal-execution release claim
       └─ #953 official + mixed/unknown E2E
```

- #967（closed；PR #977 merged）：只擁有 trusted AWS HTTPS Date
  interval clock-health authority；不擁有 DynamoDB schema、admission、
  reconcile 或 recovery 整套 store。
- #972（closed；PR #982 merged）：擁有 versioned DynamoDB schema 與 circuit
  CAS primitives；不等同完整 atomic admission lifecycle。
- #973、#983、#991、#992（均 closed）：分別擁有 compiler、executor、
  terminal reconcile/release 與 lease/crash recovery。這些已落地元件共同構成
  #956 可用的 durable admission foundation。
- #956（open）：依賴上述完整 foundation，只做 preview control-plane
  orchestration、trusted ingress assertions、zero-capture instrumentation hooks
  與 formal resource isolation。安全/成本敏感，合併前需 harper 對本 PR
  commit-bound 文件與實作重新書面 disposition。
- #964：只做 strict route/provider adapter/projector/OpenAPI/typed client；
  依賴 #956 merged，安全/成本敏感。
- #939：只做 composer 與 planning states；同時仍依賴 app shell/mobile 前置
  issue，不得以 mock planner 宣告完成。它可先整合 truthful preview，但
  本身不是任意題型 formal implementation；「任意題型正式執行」的
  acceptance/release claim blocked by #966。
- #965：formal open-intent routing contract owner（4–6h），定義解除
  `COIN_POOL`/`QUESTION_TYPES` 作為使用者題型 whitelist 的 formal bridge，
  同時保留 legacy compatibility。
- #966：formal open-intent routing implementation owner（8–12h），blocked by
  #965；完成後才解除 #939 的 formal release claim，並 blocks #953 的官方 +
  mixed/unknown E2E。

每個 issue 均不超過 12 小時；任一實作發現超過即再次拆分，不得靜默擴張。

## 10. Acceptance criteria

- [ ] Request raw body、question、locale、asset hints、UUID 與 unknown field 均按
  第 3.1 節 strict 驗證，question 與 formal 同為 trim 後 1..1000 code points。
- [ ] `asset_hints` wire value 必須 uppercase/no-whitespace/regex/case-sensitive
  unique；server 對非 canonical value 400，不 trim/uppercase/dedup。
- [ ] 任意自然語言、single/multiple/unknown、mixed language 進 Hermes；
  三官方題型只作 fixtures，沒有 client/server whitelist。
- [ ] Release-blocking：實測證明 formal raw open-intent question 不再因
  `COIN_POOL`/`QUESTION_TYPES` 被限制成三題型；#965 → #966 未完成前，#939
  不得宣稱任意題型可正式執行，#953 不得將 mixed/unknown formal E2E 標為
  通過。
- [ ] Success union 與所有 nested objects strict；無 original question、model、
  region、raw provider 欄位。
- [ ] 兩個 success variants 都有 strict `confidence` self-assessment；它不是
  calibrated probability/truth score、只作 plain text、不是 execution authorization。
- [ ] planner 無工具、connector、history、filesystem、secret、job 或 formal
  quota authority；preview 不建立 DB row、receipt、dedup lock 或 execution。
- [ ] input/output/time/attempt、identity/global rate、token/USD、circuit 數值完全
  符合第 5 節，reserve/reconcile 跨 instance 原子。
- [ ] full provider payload 由 allowed-model 精確 tokenizer 計數；>2,048
  固定 503、零截斷、零 provider call，formal submit 仍可用。
- [x] #967 trusted interval clock、#972 schema/circuit primitives、#973
  compiler、#983 executor、#991 terminal reconcile/release 與 #992
  lease/crash recovery 均已分工落地；不得把其中任一 issue 描述成整套 store。
- [ ] 由 #956 組裝既有 admission foundation，驗證單一 transaction 原子包含
  circuit condition/half-open lease、
  request count、identity/global concurrency、token/USD reserve；拒絕無 partial
  state/quota。reconcile matrix 全部以 reservation ID idempotent，concurrency
  始終 finally/TTL release，reconcile failure 鎖閉 preview。
- [ ] circuit failure classes、atomic open check、單一跨 instance half-open
  lease、success close/failure reopen 符合第 5 節；output/schema failure 與
  hostile prompt 永遠不能開 global circuit。
- [ ] request/token/USD 使用 UTC fixed 60-second 與 UTC calendar-day buckets；
  文件/產品揭露短窗 boundary burst，daily hard cap 仍成立。AWS HTTPS Date
  sample 以 monotonic send/receive、RTT、Date precision 建 trusted interval，
  admission 只以 monotonic elapsed 推進；interval 跨任何 minute/day boundary
  一律 fail closed、零 admission。refresh ≤30s、stale >90s、skew >2s、wall/monotonic anomaly、
  restart 無 fresh sample均 fail closed。
- [ ] key rotation 使用 exact dual-write；overlap ≥24h+90s 且涵蓋所有舊
  buckets/leases。admission、reservation、reconcile/release/TTL recovery
  同時且 idempotent 處理 current/previous records，任一側超 cap 即全拒絕。
- [ ] shared backend、price policy、trusted origin/topology 任一失效皆 fail closed；
  不使用現有 process-local fallback。
- [ ] forwarded headers 只在 TCP peer 精確命中 trusted proxy allowlist 時採用，
  且只接受單一合法 address；canonical binary、IPv4-mapped collapse、雙 key
  rotation exact dual-write 與 raw-IP zero persistence 均可測。
- [ ] versioned policy 精確綁 model ID、tokenizer package/version/vocab hash、
  price 與 `analysis-plan-source-classes-v1`；mismatch/unavailable fail closed，
  public 不洩漏 model。
- [ ] preview 對 formal semaphore、budget、queue、worker 使用次數皆為零；
  preview 飽和時 formal fixture 仍在 repository 既有 formal endpoint
  timeout/SLO baseline 內完成，不在本合約杜撰數字。
- [ ] raw question/clarification/prompt/response/exception 在 logs、analytics、
  traces、APM、metrics、cache 的捕捉數為零。
- [ ] `client_request_id` 僅 request-lifecycle memory，所有 persistence/log/
  trace/metadata/shared backend 捕捉數為零。
- [ ] model-derived string 僅 escaped plain text，不成為 HTML/Markdown/
  URL/action；hostile output/bidi 不改變 DOM/action。
- [ ] 所有 response 為 private/no-store、無 ETag，SW/CDN 不保存。
- [ ] 400/429/503/504 envelope 固定且不洩漏；preview failure 不阻擋另一個
  valid formal submit。
- [ ] #955 refresh → 已落地 #967/#972/#973/#983/#991/#992 foundation →
  #956 → #964 → #939（preview/composer）及
  #965 → #966 → #939 formal release claim / #953 E2E 的依賴與
  reviewer/security gates 記錄一致。

## 11. Validation plan

只在後續實作 issue 執行；本文件階段不跑完整測試。

1. Schema/property tests：extra/duplicate fields、wrong discriminator、nested
   smuggling、bounds、body >16 KiB、0/1/1000/1001 code points、whitespace、
   malformed UTF-8、combining marks、emoji、RTL、mixed scripts；兩個 success
   variants 的 confidence nested strictness、level enum 與 rationale 1/160/161；
   asset hints lowercase/whitespace/duplicate rejection 與 canonical uppercase。
2. Intent fixtures：官方三案例、自由問題、多資產、多重意圖、unknown、
   zh-TW/en/mixed language；驗證官方案例不是 whitelist。
3. Formal bridge release test（由 #966 執行、#953 驗收）：對官方三案例以外的
   single/multiple/unknown/mixed 題目，從 composer 經 formal submit 到 Hermes
   job/receipt；斷言不因固定 `QUESTION_TYPES` 被拒絕或被 client 偽裝映射，
   並保留 legacy coin/mode 相容。此項未通過即阻斷「任意題型正式分析」文案
   與 release。
4. Locale adapter tests：preview wire 僅接受 `zh-TW|en`；正式 wire 仍為
   `zh-Hant|en`，typed adapter 顯式轉換；非法 preview locale 400，不依賴
   formal fallback。
5. Authority tests：prompt injection 嘗試 tool/network/file/history/secret/job，
   斷言零 tool call、零 formal side effect。
6. Rendering tests：所有 model-derived 欄位注入 HTML/Markdown/URL scheme、
   event handler、action-like text 與 bidi controls；斷言 escaped plain text、
   無新增 DOM/action/navigation。
7. Identity/origin tests：direct forged XFF/X-Real-IP、allowlist proxy overwrite、
   non-allowlist peer ignored/fail closed、list/zone/malformed rejection、
   RFC binary canonicalization、IPv4-mapped collapse、raw-IP zero persistence、
   current/previous exact dual-write 與 purpose-separated observability、production
   enablement assertion、missing/wildcard/non-HTTPS Origin、cross-origin/CSRF。
8. Durable admission integration tests（由 #956 消費既有
   #967/#972/#973/#983/#991/#992 元件）：versioned DynamoDB schema 與 `TransactWrite`
   對 circuit/half-open、request count、identity/global concurrency、token/USD 的
   單一 atomic admission；跨 instance race/partial failure 全有或全無、拒絕
   無 partial quota。逐列驗證 reconcile matrix、reservation-id idempotency、
   owner+TTL/finally release、crash expiry、reconcile failure lockout。以 trusted
   AWS HTTPS Date + monotonic send/receive 驗 conservative interval、RTT/
   Date precision、UTC 60-second/day bucket boundary、≤30s refresh、>90s stale、
   >2s skew fail closed；以 multi-instance opposite wall-clock skew、RTT boundary
   uncertainty、wall-clock step/backward、monotonic reset、process restart 無 fresh
   sample fixtures，並以 actual time 同時位於 boundary 兩側的 interval 證明
   跨界期間 admission 次數為零，且同一 real window 不會被拆成不同 bucket。另記錄
   fixed-window burst 與 daily hard cap，並以
   mid-minute、mid-day rotation 且 old records 已有非零 count/lease 的 fixture，
   證明 rotation 前後合計永不超過 3/60s、20/day、concurrency 1；驗證任一側
   condition failure 全 transaction 零變更，以及 dual-key reconcile/release/
   TTL recovery 的 reservation-id idempotency。
9. Circuit tests：計入/排除 failure classes、atomic 第 5 次 open、open 期間零
   provider call、120 秒後唯一跨 instance half-open lease、success close、
   failure reopen、holder crash TTL recovery；大量 hostile prompts/invalid
   provider output 雖回 503 且計 admitted budget，global circuit 計數仍為零。
10. Provider tests：allowed-model exact tokenizer 覆蓋 full payload；2,048 可
    呼叫，2,049 固定 503、零截斷/零 provider call；另驗 5s timeout、6s
    deadline、attempts=1、無 retry/repair/fallback、invalid schema 固定 503；
    model/tokenizer package/version/vocab hash/source allowlist mismatch 或 unavailable
    fail closed，public response 無 model ID。
11. Privacy tests：植入 sensitive sentinel 與 `client_request_id`，掃描 app/access/error logs、
   analytics、trace/APM exporters、metric labels、cache 與 response，命中數為零。
12. Isolation tests：以 repository 既有 formal endpoint timeout/SLO 測試作
    authoritative baseline；preview concurrency=4 持續飽和時，同一 formal
    fixture 仍在該 baseline 內完成，且 instrumentation 證明 preview 對 formal
    semaphore/budget/queue/worker 使用為零。若尚無 baseline，先在實作 issue
    建立並審核，不由本文件虛構數字。
13. Header tests：success/error 都是 `private, no-store`、無 ETag、正確 Vary/
   Origin；service-worker/CDN bypass。
14. Typed client tests：strict runtime guard、abort、timeout/no retry、stale result、
    429/503/504 fallback UI contract。

每個 implementation PR 必須指定 reviewer；merge 前跑 repository local
pre-push、eye scan（涉及 UI 時）、harper 當前 commit-bound 安全/成本審查及
adversarial review。只有 CEO 親自驗證真實 branch 行為後才能回報完成。

## 12. Gate disposition

本次 refresh 改變 ownership、完成狀態與依賴敘述，因此先前 commit 的
security、adversarial 與 CEO disposition 只保留為歷史背景，不是本 PR 的
commit-bound 核准。

- 文件 refresh：已依目前 source 與 tracker 核對。
- CEO Gate A：待本 PR 新審。
- harper security/cost disposition：待本 PR 新審。
- adversarial disposition：待本 PR 新審。

在三項當前 commit-bound disposition 留下可驗證紀錄前，不得將本稿標示為
`APPROVED`、`PASS` 或已完成最終簽核。
