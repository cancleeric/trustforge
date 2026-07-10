// 輕量 runtime type guard——不引入 zod 等 schema 函式庫，只針對 UI 實際
// 讀取到的欄位做「存在 + 型別」檢查。目的是防止後端契約漂移、dev proxy
// 打錯、半成品/裁切回應等情況把非預期形狀的 `data` 一路餵進 React
// 元件，造成讀取 `undefined.xxx` 而白屏（見 codex code review）。
//
// 規則：只驗證元件實際會讀到的欄位與其基本型別，不追求對後端 schema 的
// 完整鏡像驗證（那是 `types.ts` 的靜態型別職責）。

import type {
  AdminAuditData,
  AdminAuditRecord,
  AdminBedrockView,
  AdminCapView,
  AdminConfigData,
  AdminLiveTokenView,
  AnalyzeData,
  BasisItem,
  CacheBackendStatus,
  ComparisonAnalyzeData,
  CostModelDetail,
  CostsData,
  LedgerRunRecord,
  CrossSourceSignal,
  Evidence,
  FreshnessEntry,
  HealthData,
  HistoryData,
  OverviewCoin,
  OverviewData,
  PriceProvenance,
  PriceProvenanceEntry,
  ReputationTraceEntry,
  Report,
  StancePair,
  StatusData,
  TrustComponentsAggregate,
  TrustHistoryEntry,
  TrustRadar,
  TrustRadarDimension,
} from './types'

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'string')
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'number')
}

// #1 修復（legacy 快照炸掉 React overview）：`isDecisionState` 嚴格比對三態
// 字面值，用在「擋整包 entry」的位置太嚴——legacy 快照／版本切換期可能完全
// 沒有 `decision_state` 這個 key，或帶尚未認識的新 enum 值，跟 SSR
// （`fetch_scheduler.py` 對缺失/未知值一律當 normal 處理）行為不一致，會讓
// 單一舊快照就讓整個 `/api/overview`／`/api/analyze` payload 判定失敗。
// 這裡只在「形狀」層面放行缺失（`undefined`）與任意字串（含未知字面值）；
// 真正型別錯誤（數字/物件/陣列等非字串）才擋。實際渲染前一律再用
// `normalizeDecisionState()`（見 `types.ts`）正規化為 `'normal'`，兩邊
// 同一套 fallback 規則。
function isDecisionStateOrLegacy(value: unknown): boolean {
  return value === undefined || typeof value === 'string'
}

// ── /api/overview ────────────────────────────────────────────────────────

// 後端 `_snapshot_dict()`（scripts/fetch_scheduler.py）：`reputation_trace`
// 是**選填**欄位——`evidence` 為空/None，或該幣本輪沒有可用的動態信譽
// trace 時，這個 key 完全不會出現在快照裡（`test_snapshot_dict_omits_
// reputation_trace_key_when_no_evidence` 明確斷言此為合法情況），不是漏
// 資料。原本要求它必為物件會把這種合法「無 trace」快照當成畸形而
// parse_error，誤殺整張總覽卡（codex code review：過嚴驗證比不驗更糟）。
// 規則：key 不存在 → 放行；key 存在 → 仍要求是物件、且每筆 entry 型別
// 正確（prior/final/delta/agree_n/contradict_n 皆為 number）。
function isReputationTraceEntry(value: unknown): value is ReputationTraceEntry {
  return (
    isPlainObject(value) &&
    typeof value.prior === 'number' &&
    typeof value.final === 'number' &&
    typeof value.delta === 'number' &&
    typeof value.agree_n === 'number' &&
    typeof value.contradict_n === 'number'
  )
}

function isReputationTrace(value: unknown): value is Record<string, ReputationTraceEntry> {
  return isPlainObject(value) && Object.values(value).every(isReputationTraceEntry)
}

// codex 窮舉終審 MEDIUM 修復（越界值穿透成假低風險）：原本只驗
// `typeof value.manip_score === 'number'`，`NaN`/`Infinity`/負值/大於 1
// 的值都會通過 `typeof` 檢查（JS 裡這些全部是 `typeof number`），一路
// 餵進 `manipRiskDisplay()` 的門檻比較，例如負值必然 < MEDIUM 門檻，會
// 被誤判成「低操縱風險」——把畸形資料偽裝成一個確定的安全結論，比顯式
// 「未評分」更危險。改用 `Number.isFinite` 排除 `NaN`/`Infinity`，並限制
// 在 `_calc_manip_signal()`（後端）保證的合法值域 0..1 內；越界值視同
// 「這個欄位形狀就是壞的」，讓 `isOverviewCoin` 直接判定不合法（跟其他
// 欄位型別錯誤時的既有處理方式一致），而不是放行後在下游用 fail-closed
// 補救——validator 層先擋掉才是治本，`manipRiskDisplay()` 的 fail-closed
// 是第二道防線（見該函式）。
function isManipScoreValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
}

function isOverviewCoin(value: unknown): value is OverviewCoin {
  if (!isPlainObject(value)) return false
  return (
    typeof value.coin === 'string' &&
    typeof value.trust_score === 'number' &&
    typeof value.direction === 'string' &&
    typeof value.calibrated_confidence === 'number' &&
    isDecisionStateOrLegacy(value.decision_state) &&
    typeof value.generated_at === 'string' &&
    (value.reputation_trace === undefined || isReputationTrace(value.reputation_trace)) &&
    // #86：`manip_score`／`manip_score_mean` 同 `reputation_trace` 款選填
    // 慣例——舊格式快照／本輪無 evidence 的快照合法不帶這兩個 key，不要求
    // 必為 number 而誤殺整張總覽卡（`test_snapshot_dict_omits_manip_score_
    // keys_when_no_evidence` 明確斷言此為合法情況）。
    //
    // codex 複審 delta HIGH 修復：「有 manip_score 但無 manip_score_mean」
    // （legacy payload：舊 writer 只寫平均值語意的 manip_score）在這裡仍
    // 視為合法形狀（兩欄位是否成對出現不在這裡擋）——那是「哪個版本的
    // writer 寫的」語意判斷，下放給 `manipRiskDisplay()`（見 `manipRisk.ts`
    // docstring）。但兩欄位個別的**數值合法性**（是否為 0..1 的有限實數）
    // 屬於形狀範疇，就在這裡擋（見下方 `isManipScoreValue`），因為 validator
    // 的職責是「JSON 形狀正確嗎」，不是「這個數字現在能不能拿來分級」。
    (value.manip_score === undefined || isManipScoreValue(value.manip_score)) &&
    (value.manip_score_mean === undefined || isManipScoreValue(value.manip_score_mean)) &&
    typeof value.fetched_at_epoch === 'number'
  )
}

export function isOverviewData(value: unknown): value is OverviewData {
  return isPlainObject(value) && Array.isArray(value.coins) && value.coins.every(isOverviewCoin)
}

// ── /api/health ──────────────────────────────────────────────────────────

export function isHealthData(value: unknown): value is HealthData {
  return (
    isPlainObject(value) &&
    typeof value.status === 'string' &&
    typeof value.version === 'string' &&
    typeof value.uptime_seconds === 'number'
  )
}

// ── /api/analyze ─────────────────────────────────────────────────────────

function isBasisItem(value: unknown): value is BasisItem {
  return (
    isPlainObject(value) &&
    typeof value.claim === 'string' &&
    typeof value.explanation === 'string' &&
    isNumberArray(value.evidence_idx)
  )
}

function isStancePair(value: unknown): value is StancePair {
  return (
    isPlainObject(value) &&
    typeof value.source === 'string' &&
    typeof value.stance === 'string' &&
    (value.claim_id === undefined || typeof value.claim_id === 'string') &&
    (value.text === undefined || typeof value.text === 'string')
  )
}

// `CrossSourceSignalPanel` 對 `stance_pairs` 做 `signal.stance_pairs ?? []`
// 後直接 `for...of` 疊代——若欄位存在但不是陣列（例如是數字/字串），`??`
// 不會擋下非 nullish 的畸形值，`for...of` 對不可疊代的值會直接 throw，
// 一樣是「過 guard 卻在 render 時炸」的洞，故此處也要嚴格驗證。
function isCrossSourceSignal(value: unknown): value is CrossSourceSignal | null {
  if (value === null) return true
  if (!isPlainObject(value)) return false
  if (value.type !== 'divergence' && value.type !== 'consensus') return false
  if (typeof value.summary !== 'string') return false
  if (value.stance_pairs !== undefined) {
    if (!Array.isArray(value.stance_pairs) || !value.stance_pairs.every(isStancePair)) return false
  }
  // `distinct_sources`（#13 去重欄位）：選填，缺欄位（舊資料/尚未升級的
  // 後端 stale 快取）放行——`groupByStance` 有 client 端 fallback 自行依
  // source 去重，這是刻意的向後相容，不可因缺這個欄位就整包 parse_error。
  // 但**存在時**必須是 `{bullish: StancePair[], bearish: StancePair[]}`
  // 形狀——`groupByStance` 優先讀這個欄位直接餵進 `SideColumn` 的
  // `.map()`，畸形值（如 `bullish` 不是陣列）會在 render 時 `.map()` 炸
  // 掉，跟 `/costs` parse_error 那次同一類洞，故比照 `stance_pairs` 嚴格
  // 驗證。
  if (value.distinct_sources !== undefined) {
    const ds = value.distinct_sources
    if (
      !isPlainObject(ds) ||
      !Array.isArray(ds.bullish) ||
      !ds.bullish.every(isStancePair) ||
      !Array.isArray(ds.bearish) ||
      !ds.bearish.every(isStancePair)
    ) {
      return false
    }
  }
  if (value.supporting_claim_ids !== undefined && !isStringArray(value.supporting_claim_ids)) {
    return false
  }
  if (value.objective_direction !== undefined && typeof value.objective_direction !== 'string') {
    return false
  }
  if (value.sentiment_direction !== undefined && typeof value.sentiment_direction !== 'string') {
    return false
  }
  // issue #21：`sentiment_source_count` 選填（僅主分支回傳值才有，
  // `_stance_pair_signal()` 備援分支/舊快照皆可能沒有這個 key，放行缺
  // 欄位）；存在時 `CrossSourceSignalPanel` 直接拿去做 `=== 1` 比較，畸形
  // 型別（如字串/物件）不會 throw 但會讓判斷永遠是 false 而悄悄漏顯示徽章
  // ——比照本檔其餘欄位慣例，型別不對就整包 parse_error，不悄悄放行。
  if (
    value.sentiment_source_count !== undefined &&
    typeof value.sentiment_source_count !== 'number'
  ) {
    return false
  }
  return true
}

function isReport(value: unknown): value is Report {
  return (
    isPlainObject(value) &&
    typeof value.coin === 'string' &&
    typeof value.question_type === 'string' &&
    typeof value.question === 'string' &&
    typeof value.market_judgment === 'string' &&
    isStringArray(value.facts) &&
    isStringArray(value.inferences) &&
    Array.isArray(value.key_basis) &&
    value.key_basis.every(isBasisItem) &&
    typeof value.confidence === 'number' &&
    isStringArray(value.limits) &&
    isStringArray(value.could_flip) &&
    isStringArray(value.contrarian) &&
    typeof value.generated_at === 'string' &&
    typeof value.direction === 'string' &&
    isCrossSourceSignal(value.cross_source_signal) &&
    typeof value.calibrated_confidence === 'number' &&
    isDecisionStateOrLegacy(value.decision_state)
  )
}

function isEvidence(value: unknown): value is Evidence {
  return (
    isPlainObject(value) &&
    typeof value.source === 'string' &&
    typeof value.fetched_at === 'string' &&
    typeof value.content_reference === 'string' &&
    typeof value.related_claim === 'string' &&
    typeof value.source_url === 'string' &&
    typeof value.kind === 'string' &&
    typeof value.trust === 'number' &&
    isPlainObject(value.trust_components) &&
    isStringArray(value.flags) &&
    isStringArray(value.info_flags)
  )
}

// `TrustRadarChart` 讀 `d.has_data`(boolean 判斷分支)、`d.trust`(number|null，
// 直接 `.toFixed`/算術)、`d.label`(string)、`d.n_sources`/`d.n_evidence`
// (number，直接顯示)、`d.single_source`(經 `Boolean()` 包過，可以寬鬆，但
// 仍限制在 boolean|null，避免非預期物件/陣列悄悄混過)。原本
// `isAnalyzeData` 只驗 `trust_radar` 是物件、沒驗每個維度的值，
// `{price:null}` 這類會過 guard 但在 `d.has_data` 讀取時直接白屏。
function isTrustRadarDimension(value: unknown): value is TrustRadarDimension {
  return (
    isPlainObject(value) &&
    typeof value.label === 'string' &&
    typeof value.has_data === 'boolean' &&
    (value.trust === null || typeof value.trust === 'number') &&
    typeof value.n_sources === 'number' &&
    typeof value.n_evidence === 'number' &&
    (value.single_source === null || typeof value.single_source === 'boolean')
  )
}

function isTrustRadar(value: unknown): value is TrustRadar {
  return isPlainObject(value) && Object.values(value).every(isTrustRadarDimension)
}

// `PriceProvenancePanel` 讀 `entry.source_url`(丟進 safeHref)、
// `entry.content_reference`/`entry.fetched_at`(直接渲染文字)。原本只驗
// `price_provenance` 是物件，`{ohlcv:null}` 會過 guard 但 render 時讀
// `entry.source_url` 直接白屏。
function isPriceProvenanceEntry(value: unknown): value is PriceProvenanceEntry {
  return (
    isPlainObject(value) &&
    typeof value.content_reference === 'string' &&
    typeof value.fetched_at === 'string' &&
    typeof value.source_url === 'string'
  )
}

function isPriceProvenance(value: unknown): value is PriceProvenance {
  return isPlainObject(value) && Object.values(value).every(isPriceProvenanceEntry)
}

function isTrustComponentsAggregate(value: unknown): value is TrustComponentsAggregate {
  return (
    isPlainObject(value) &&
    typeof value.reputation === 'number' &&
    typeof value.corroboration === 'number' &&
    typeof value.recency === 'number' &&
    typeof value.manipulation === 'number'
  )
}

export function isAnalyzeData(value: unknown): value is AnalyzeData {
  return (
    isPlainObject(value) &&
    typeof value.version === 'string' &&
    isReport(value.report) &&
    Array.isArray(value.evidence) &&
    value.evidence.every(isEvidence) &&
    isTrustRadar(value.trust_radar) &&
    isTrustComponentsAggregate(value.trust_components_aggregate) &&
    isPriceProvenance(value.price_provenance)
  )
}

// `/api/analyze?type=comparison`：`report_a`/`report_b` 等全套欄位各驗一次，
// 沿用同一批 `isReport`/`isEvidence`/`isTrustRadar`/
// `isTrustComponentsAggregate`/`isPriceProvenance` guard——跟單幣
// `isAnalyzeData` 是同一個 source of truth，不會分岔。
export function isComparisonAnalyzeData(value: unknown): value is ComparisonAnalyzeData {
  return (
    isPlainObject(value) &&
    typeof value.version === 'string' &&
    isReport(value.report_a) &&
    Array.isArray(value.evidence_a) &&
    value.evidence_a.every(isEvidence) &&
    isTrustRadar(value.trust_radar_a) &&
    isTrustComponentsAggregate(value.trust_components_aggregate_a) &&
    isPriceProvenance(value.price_provenance_a) &&
    isReport(value.report_b) &&
    Array.isArray(value.evidence_b) &&
    value.evidence_b.every(isEvidence) &&
    isTrustRadar(value.trust_radar_b) &&
    isTrustComponentsAggregate(value.trust_components_aggregate_b) &&
    isPriceProvenance(value.price_provenance_b)
  )
}

// ── /api/status ──────────────────────────────────────────────────────────

// `StatusPage` 讀 `entry.status`（三態分支判斷顏色）、`entry.source`/
// `entry.coin`（直接渲染文字）、`entry.age_seconds`（`status!=="missing"`
// 時直接算術/`.toFixed`）——status 必須是三個合法值之一，否則未知狀態會
// 讓分支判斷 fallback 到非預期顏色但不至於白屏；但 age_seconds/fetched_at
// 型別錯誤（如物件）會在算術/渲染時炸，故仍嚴格檢查。
function isFreshnessEntry(value: unknown): value is FreshnessEntry {
  return (
    isPlainObject(value) &&
    typeof value.source === 'string' &&
    typeof value.coin === 'string' &&
    (value.status === 'fresh' || value.status === 'stale' || value.status === 'missing') &&
    (value.fetched_at === null || typeof value.fetched_at === 'number') &&
    (value.age_seconds === null || typeof value.age_seconds === 'number')
  )
}

function isCacheBackendStatus(value: unknown): value is CacheBackendStatus {
  return (
    isPlainObject(value) &&
    typeof value.name === 'string' &&
    typeof value.connected === 'boolean' &&
    typeof value.primary_connected === 'boolean' &&
    typeof value.active_backend === 'string' &&
    typeof value.degraded === 'boolean'
  )
}

export function isStatusData(value: unknown): value is StatusData {
  if (!isPlainObject(value)) return false
  if (
    typeof value.version !== 'string' ||
    typeof value.uptime_seconds !== 'number' ||
    typeof value.bedrock_capable !== 'boolean' ||
    typeof value.live_token_set !== 'boolean' ||
    !isCacheBackendStatus(value.cache_backend)
  ) {
    return false
  }
  const freshness = value.freshness
  return (
    isPlainObject(freshness) &&
    typeof freshness.fresh === 'number' &&
    typeof freshness.stale === 'number' &&
    typeof freshness.missing === 'number' &&
    Array.isArray(freshness.entries) &&
    freshness.entries.every(isFreshnessEntry)
  )
}

// ── /api/costs ───────────────────────────────────────────────────────────

function isCostModelDetail(value: unknown): value is CostModelDetail {
  return (
    isPlainObject(value) &&
    typeof value.cost_usd === 'number' &&
    typeof value.tokens_in === 'number' &&
    typeof value.tokens_out === 'number'
  )
}

// codex HIGH（成本端點可擴展性）修復後，後端 `/api/costs` 回有界摘要：
// `run_count`（真實總筆數）+ 最近 N 筆 `runs`（後端 cap，目前 50）。`runs`
// 現在是有界欄位，逐筆驗證欄位型別的成本可控，用來畫「最近 N 筆」明細表。
function isLedgerRunRecord(value: unknown): value is LedgerRunRecord {
  return (
    isPlainObject(value) &&
    typeof value.ts === 'string' &&
    typeof value.total_cost_usd === 'number' &&
    Array.isArray(value.calls) &&
    (value.coin === undefined || typeof value.coin === 'string') &&
    (value.question_type === undefined || typeof value.question_type === 'string') &&
    (value.offline === undefined || typeof value.offline === 'boolean')
  )
}

export function isCostsData(value: unknown): value is CostsData {
  if (!isPlainObject(value)) return false
  if (typeof value.total_cost_usd !== 'number') return false
  if (typeof value.run_count !== 'number') return false
  if (!isPlainObject(value.by_model)) return false
  if (!Object.values(value.by_model).every((v) => typeof v === 'number')) return false
  if (!isPlainObject(value.by_model_detail)) return false
  if (!Object.values(value.by_model_detail).every(isCostModelDetail)) return false
  if (!Array.isArray(value.runs)) return false
  if (!value.runs.every(isLedgerRunRecord)) return false
  return true
}

// ── /api/history ─────────────────────────────────────────────────────────

function isTrustHistoryEntry(value: unknown): value is TrustHistoryEntry {
  return (
    isPlainObject(value) &&
    typeof value.date === 'string' &&
    typeof value.coin === 'string' &&
    typeof value.trust_score === 'number' &&
    typeof value.direction === 'string' &&
    typeof value.calibrated_confidence === 'number' &&
    isDecisionStateOrLegacy(value.decision_state) &&
    typeof value.generated_at === 'string' &&
    (value.reputation_trace === undefined || isReputationTrace(value.reputation_trace))
  )
}

export function isHistoryData(value: unknown): value is HistoryData {
  return (
    isPlainObject(value) &&
    typeof value.coin === 'string' &&
    typeof value.days === 'number' &&
    Array.isArray(value.history) &&
    value.history.every(isTrustHistoryEntry)
  )
}

// ── /api/admin/* ─────────────────────────────────────────────────────────
// 對應 `web.py::_admin_config_view()`；管理頁每個欄位都會直接渲染
// effective/source/updated_*，缺欄或型別錯就整包 parse_error（不讓半成品
// 設定狀態進表單——管理面顯示錯值比白屏更危險：可能誤導管理員做出錯誤
// 的開關/額度決策）。`source` 只驗字串不窮舉 enum（後端可能演進出新層
// 級字面值，如 env kill-switch/config_read_error，顯示層原樣渲染即可）。

function isAdminCapView(value: unknown): value is AdminCapView {
  return (
    isPlainObject(value) &&
    (value.config === null || typeof value.config === 'number') &&
    (value.env === null || typeof value.env === 'string') &&
    typeof value.default === 'number' &&
    typeof value.effective === 'number' &&
    typeof value.source === 'string'
  )
}

function isAdminBedrockView(value: unknown): value is AdminBedrockView {
  return (
    isPlainObject(value) &&
    (value.config === null || typeof value.config === 'boolean') &&
    typeof value.bedrock_model_id_set === 'boolean' &&
    typeof value.effective === 'boolean' &&
    typeof value.source === 'string'
  )
}

function isAdminLiveTokenView(value: unknown): value is AdminLiveTokenView {
  return (
    isPlainObject(value) &&
    typeof value.config_configured === 'boolean' &&
    (value.config_last4 === null || typeof value.config_last4 === 'string') &&
    typeof value.env_configured === 'boolean' &&
    typeof value.effective_configured === 'boolean' &&
    typeof value.source === 'string'
  )
}

export function isAdminConfigData(value: unknown): value is AdminConfigData {
  return (
    isPlainObject(value) &&
    isAdminCapView(value.daily_cap_usd) &&
    isAdminBedrockView(value.bedrock_enabled) &&
    isAdminLiveTokenView(value.live_token) &&
    (value.version === null || typeof value.version === 'number') &&
    (value.updated_at === null || typeof value.updated_at === 'string') &&
    (value.updated_by === null || typeof value.updated_by === 'string') &&
    typeof value.exists === 'boolean' &&
    typeof value.version_corrupt === 'boolean' &&
    // PUT 200 才帶 warnings；GET 合法缺席。存在但形狀畸形（非字串陣列）
    // 要擋——頁面會逐條渲染 warnings，不能讓 `.map()` 在 render 時炸掉。
    (value.warnings === undefined || isStringArray(value.warnings))
  )
}

function isAdminAuditRecord(value: unknown): value is AdminAuditRecord {
  if (!isPlainObject(value)) return false
  if (value.ts !== null && typeof value.ts !== 'string') return false
  if (value.actor !== null && typeof value.actor !== 'string') return false
  if (!Array.isArray(value.changes)) return false
  for (const change of value.changes) {
    // `old`/`new` 刻意不限型別（cap 是數字、開關是 bool、token 是遮罩
    // 字串、清除是 null——顯示層用 formatAuditValue 統一轉字串），但
    // entry 本身必須是帶 `field` 字串的物件，否則表格渲染直接讀爆。
    if (!isPlainObject(change) || typeof change.field !== 'string') return false
  }
  if (value.version_from !== null && typeof value.version_from !== 'number') return false
  if (value.version_to !== null && typeof value.version_to !== 'number') return false
  if (value.user_agent !== null && typeof value.user_agent !== 'string') return false
  return true
}

export function isAdminAuditData(value: unknown): value is AdminAuditData {
  return (
    isPlainObject(value) &&
    typeof value.limit === 'number' &&
    Array.isArray(value.records) &&
    value.records.every(isAdminAuditRecord)
  )
}
