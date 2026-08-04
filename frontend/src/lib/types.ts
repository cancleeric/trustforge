// TrustForge API 型別定義——對應後端 `src/trustforge/schema.py` 與
// `src/trustforge/web.py` `/api/*` 各端點回傳結構（見
// docs/architecture/PLAN-frontend-backend-split.md §2）。所有型別皆為唯讀資料模型，不含
// 任何邏輯；前端只消費、不推導後端未提供的欄位。

/** 統一信封：所有 `/api/*` 端點共用同一種成功/失敗形狀。 */
export type ApiEnvelope<T> = ApiSuccess<T> | ApiFailure

export interface ApiSuccess<T> {
  ok: true
  data: T
}

export interface ApiFailure {
  ok: false
  error: {
    code: string
    message: string
    /** 部分端點（如 Hermes planning preview）的安全錯誤契約會明示是否可重試。 */
    retryable?: boolean
    retry_href?: string
    /** `/api/admin/config` PUT 409（`version_conflict`）專用：最新
     * version（後端衝突後重讀失敗時為 null）——管理頁重載最新設定後
     * 再改，見 `AdminPage.tsx` 409 處理。 */
    current_version?: number | null
  }
}

export type DecisionState = 'abstain' | 'low_confidence' | 'normal'

/** #1 修復：legacy 快照／版本切換期可能完全缺 `decision_state` 欄位，或帶
 * 尚未認識的新 enum 字面值——validators.ts 只在「形狀」層面放行這兩種
 * 情況（不整包 parse_error），實際渲染前一律在這裡正規化為 `'normal'`，
 * 跟 SSR（`web.py`/`fetch_scheduler.py` 對缺失/未知值一律走 `normal`
 * 分支的既有行為）同一套 fallback 規則，避免版本切換期兩端行為分裂。
 * 所有讀取 `decision_state` 來決定 hero 數字／配色／徽章文字的元件都必須
 * 先經過這個函式，不得直接對原始值做三態比對。 */
export function normalizeDecisionState(value: unknown): DecisionState {
  return value === 'abstain' || value === 'low_confidence' ? value : 'normal'
}

// ── /api/overview ──────────────────────────────────────────────────────────

export interface ReputationTraceEntry {
  prior: number
  final: number
  delta: number
  agree_n: number
  contradict_n: number
}

export interface OverviewCoin {
  coin: string
  trust_score: number
  direction: string
  calibrated_confidence: number
  decision_state: DecisionState
  generated_at: string
  /** 選填：後端 `_snapshot_dict()` 在該幣本輪無 evidence／無動態信譽
   * trace 時完全不會帶這個 key（非漏資料，見 validators.ts 說明）。 */
  reputation_trace?: Record<string, ReputationTraceEntry>
  /** #86 選填，codex 複審 HIGH 修復後語意：跨幣操縱風險排行用的
   * **worst-case（max）操縱懲罰分**（`_calc_manip_signal()`，對 evidence
   * 逐筆 `trust_components["manipulation"]` 取 `max()`，0～1，越高代表操
   * 縱風險越高）——刻意不是平均：平均會被 evidence 筆數稀釋，讓「多筆
   * 乾淨證據裡混一筆已確認操縱」被沖淡成低分，誤判低風險。只要有一筆
   * 已確認操縱，這個值就會反映出來，見 `lib/manipRisk.ts` 分級邏輯。
   * 舊格式快照（本欄位新增前寫入的）／本輪無 evidence 的快照都合法不帶
   * 這個 key（同 `reputation_trace` 慣例，非漏資料）——前端遇到缺席時
   * 必須顯式標「未評分」中性態（`ManipRiskBadge`），不能悄悄不顯示、更
   * 不能當成「風險低」。 */
  manip_score?: number
  /** #86 選填：`manip_score` 同批 evidence 的算術平均，**僅供輔助判讀**
   * （UI 呈現於徽章 tooltip），不參與風險分級——分級一律只吃
   * `manip_score`（worst-case）。缺席規則同 `manip_score`。 */
  manip_score_mean?: number
  fetched_at_epoch: number
}

export interface OverviewData {
  coins: OverviewCoin[]
}

// ── /api/analyze ────────────────────────────────────────────────────────────

export interface BasisItem {
  claim: string
  explanation: string
  evidence_idx: number[]
}

export interface Evidence {
  source: string
  fetched_at: string
  content_reference: string
  related_claim: string
  source_url: string
  kind: string
  /** Claim-level direction. Optional while legacy snapshots age out. */
  direction?: 'bullish' | 'bearish' | 'neutral' | string
  trust: number
  trust_components: Record<string, number>
  /** 確定判定為操縱的紅旗，已反映在 trust 分數。 */
  flags: string[]
  /** W3：中性資訊提示（如高相似度叢集），不代表操縱判定、不影響 trust。 */
  info_flags: string[]
  /** 官方 CSV/檔案型資料的可重現血緣；非檔案來源不帶此欄位。 */
  data_lineage?: DataLineage | null
}

export interface DataLineage {
  dataset_role: string
  dataset_name: string
  dataset_generated_at: string
  file: string
  sha256: string
  rows: number
  coverage: { start_date: string; end_date: string }
  analysis_window: string
  trading_pair: string
  time_basis: string
  interval: string
  price_unit: string
  columns: string[]
}

/** #862 事實聚合群組：同源同指標的時序 Evidence 聚合為顯示群組。 */
export interface EvidenceGroup {
  /** 群組代表 Evidence 在 evidence[] 中的索引（trust 最高者）。 */
  representative_idx: number
  /** 群組所有成員在 evidence[] 中的索引（含代表自己）。 */
  member_indices: number[]
  /** 趨勢方向。null 表示無法判定（如群組只有單一數值）。 */
  trend: 'rising' | 'falling' | 'stable' | null
  /** 值域字串，如 "828–891 TH/s"。null 表示非數值型內容。 */
  value_range: string | null
  /** 最近一筆的數值摘要。null 表示無法提取。 */
  latest_value: string | null
}

export interface StancePair {
  source: string
  stance: string
  claim_id?: string
  text?: string
}

export interface CrossSourceSignal {
  type: 'divergence' | 'consensus'
  summary: string
  supporting_claim_ids?: string[]
  /** 原始逐筆明細（去重鍵是 claim_id），供展開查看每一則矛盾主張；
   *  不代表獨立來源數，計數/去重渲染請用 `distinct_sources`（#13）。 */
  stance_pairs?: StancePair[]
  /** `stance_pairs` 依 source 在各自陣營（bullish/bearish）內去重後的代表清單，
   *  同一來源在同一陣營只留一筆——UI 計數/去重渲染的正確資料源頭（#13）。 */
  distinct_sources?: { bullish: StancePair[]; bearish: StancePair[] }
  objective_direction?: string
  sentiment_direction?: string
  /** issue #21（CISO-LOW）：情緒類（news/social/sentiment）這一輪算出
   *  `sentiment_direction` 時實際涉及的獨立來源數（後端 `sent_sources |
   *  stance_pairs 來源` 聯集，見 PR #135 R1 修法）。純展示透明化欄位，不
   *  影響任何分數/方向計算——只在 `agent.orchestrator.detect_cross_
   *  source_signal` 的 obj_dir/sent_dir 主分支回傳值出現，`_stance_pair_
   *  signal()` 備援分支（已保證 >=2 獨立來源）不會有這個欄位。`=== 1` 時
   *  UI 顯示「單一來源主導」透明徽章，提醒該類判定目前只有 1 個獨立來源
   *  佐證，避免被誤讀成多源共識/背離。
   *
   *  Normalized string key count：計數用後端 `_normalize_source_key`
   *  （`strip().casefold()`）正規化去重，只收斂同一 publisher 的大小寫/
   *  空白變體（如 `"CoinDesk"`/`" coindesk "`），**不解 publisher 別名**
   *  （如 `coindesk` vs `coindesk.com` 仍視為 2 個不同來源）——別名映射見
   *  follow-up issue #72，本輪不做 canonicalization。 */
  sentiment_source_count?: number
}

export interface Report {
  coin: string
  question_type: string
  question: string
  market_judgment: string
  facts: string[]
  inferences: string[]
  key_basis: BasisItem[]
  confidence: number
  limits: string[]
  could_flip: string[]
  contrarian: string[]
  generated_at: string
  direction: string
  cross_source_signal: CrossSourceSignal | null
  /** Phase 1 獨特洞察層（#24/#15/#21/#72）：非顯而易見、可驗證的信任洞察
   * 清單。每條攜「兩個以上貢獻來源 + 方向 + 強度 + 資料覆蓋閘」，供前端
   * InsightExplainabilityPanel 渲染可解釋溯源。覆蓋不足時 `coverage` 為
   * "insufficient"、summary 含「無法判定」，UI 必須顯式標註、不補 0。選填：
   * 舊快照／本欄位新增前的報告合法不帶（前端視為 []）。 */
  insights?: Insight[]
  /** D1.5 假設驗證題型結構化正反方帳本（見 `HypothesisLedger`）。選填。 */
  hypothesis_ledger?: HypothesisLedger | null
  calibrated_confidence: number
  decision_state: DecisionState
  asset_context?: AssetContext | null
  risk_notices?: RiskNotice[]
  /** #758：資產內在事實的觀察性 shadow 結果。舊快照可缺席；刻意保留
   * unknown，渲染層必須先做 fail-closed shape validation，避免版本切換期
   * 的畸形 optional payload 拖垮整份正式報告。 */
  asset_intrinsic_assessment?: unknown
  /** #862 事實聚合群組：同源同指標的時序 Evidence 聚合為顯示群組。
   * 選填：舊快照或未計算聚合的報告合法不帶此欄位（前端退回 flat 模式）。 */
  evidence_groups?: EvidenceGroup[] | null
}

// ── Asset taxonomy（對應後端 trustforge/asset_context.py enum）────────────
// 所有 enum 型別皆保留 "unknown" 值——後端認定不明的欄位絕不用空字串冒充。

export type AssetSector =
  | 'defi'
  | 'l1'
  | 'l2'
  | 'stablecoin'
  | 'exchange'
  | 'infrastructure'
  | 'meme'
  | 'rwa'
  | 'gaming'
  | 'ai'
  | 'unknown'

export type AssetLayer =
  | 'layer_1'
  | 'layer_2'
  | 'app'
  | 'protocol'
  | 'token'
  | 'offchain'
  | 'unknown'

export type TokenRole =
  | 'gas'
  | 'governance'
  | 'utility'
  | 'staking'
  | 'stable'
  | 'lp'
  | 'wrapped'
  | 'meme'
  | 'unknown'

export type MarketCapTier =
  | 'large'
  | 'mid'
  | 'small'
  | 'micro'
  | 'unknown'

/** Asset taxonomy enum value sets（對應後端 ASSET_SECTORS / ASSET_LAYERS /
 * TOKEN_ROLES / MARKET_CAP_TIERS），供前端驗證與 lookup table 覆蓋稽核用。 */
export const ASSET_SECTORS: readonly AssetSector[] = [
  'defi', 'l1', 'l2', 'stablecoin', 'exchange',
  'infrastructure', 'meme', 'rwa', 'gaming', 'ai', 'unknown',
] as const

export const ASSET_LAYERS: readonly AssetLayer[] = [
  'layer_1', 'layer_2', 'app', 'protocol', 'token', 'offchain', 'unknown',
] as const

export const TOKEN_ROLES: readonly TokenRole[] = [
  'gas', 'governance', 'utility', 'staking', 'stable', 'lp', 'wrapped', 'meme', 'unknown',
] as const

export const MARKET_CAP_TIERS: readonly MarketCapTier[] = [
  'large', 'mid', 'small', 'micro', 'unknown',
] as const

// ── AssetContext ──────────────────────────────────────────────────────────

export interface AssetContext {
  schema_version: string
  asset_id: string
  symbol: string
  name: string
  sector: AssetSector
  layer: AssetLayer
  token_role: TokenRole
  market_cap_tier: MarketCapTier
  ecosystem: string | null
  parent_asset_id: string | null
  tags: string[]
  /** L2 結算鏈（例：ARB → "ethereum"）。舊快照可能未帶，缺省為 "unknown"。 */
  settlement_chain?: string
  /** 交易手續費計價代幣（例：ARB 轉帳仍需 ETH 付 gas）。缺省為 "unknown"。 */
  gas_token?: string
  /** 上下游依賴（白話關聯說明的資料來源，例：sequencer、canonical_bridge）。 */
  dependencies?: string[]
}

/** 型別層面不看 enum 白名單（比照 runtime `isAssetContext` 策略）——
 * 後端新增 enum 值時前端型別不該連帶編譯失敗，由渲染層 fallback。 */
export function isKnownAssetSector(value: string): value is AssetSector {
  return (ASSET_SECTORS as readonly string[]).includes(value)
}

export function isKnownAssetLayer(value: string): value is AssetLayer {
  return (ASSET_LAYERS as readonly string[]).includes(value)
}

export function isKnownTokenRole(value: string): value is TokenRole {
  return (TOKEN_ROLES as readonly string[]).includes(value)
}

export function isKnownMarketCapTier(value: string): value is MarketCapTier {
  return (MARKET_CAP_TIERS as readonly string[]).includes(value)
}

export function isKnownDependencyKind(value: string): value is DependencyKind {
  return (DEPENDENCY_KINDS as readonly string[]).includes(value)
}

export function isKnownImpactDirection(value: string): value is ImpactDirection {
  return (IMPACT_DIRECTIONS as readonly string[]).includes(value)
}

export function isKnownUpgradeEventStatus(value: string): value is UpgradeEventStatus {
  return (UPGRADE_EVENT_STATUSES as readonly string[]).includes(value)
}

export interface RiskNotice {
  code: string
  severity: 'info' | 'warning'
  message: string
}

// ── /api/asset-context ───────────────────────────────────────────────────

/** `GET /api/asset-context?symbol=` 回應資料——查無資料時 `asset_context`
 * 為 `null`（HTTP 200，非 404/500，見 `web.py::_handle_api_asset_context`
 * docstring）。 */
export interface AssetContextResponseData {
  asset_context: AssetContext | null
}

// ── /api/peer-metrics ────────────────────────────────────────────────────

/** 一格量測值（TPS/TVL/Gas fee/活動細項共用形狀）。`value` 可能是
 * `null`（該指標缺席，見 `docs/api/openapi.yaml` peer-metrics 說明），
 * 前端一律誠實顯示「—」，不補 0。 */
export interface PeerMetricValue {
  value: number | null
  unit: string
  method: string
  source: string
}

/** `/api/peer-metrics` 單一資產的 snapshot——`additionalProperties: true`，
 * 這裡只收斂目前已知欄位；`activity_breakdown` 是分項活躍度（例：
 * swap/bridge），鍵名不固定。 */
export interface PeerMetricsSnapshot {
  asset_id: string
  observed_tps?: PeerMetricValue | null
  tvl?: PeerMetricValue | null
  gas_fee?: PeerMetricValue | null
  activity_breakdown?: Record<string, PeerMetricValue> | null
  window_start?: string
  window_end?: string
  observed_at?: string
}

/** 同層 peer 的比較結果。`asset_id` 恆存在（宣告在 peer_group 裡的
 * peer 一律列出，即使查無 snapshot 也不靜默丟棄，見 `web.py`
 * `_handle_api_peer_metrics` docstring / PR #653 codex-review）。
 * `snapshot` 在該 peer 查無資料時為 `null`（此時 `comparable` 恆
 * `false`）。`comparable: false` 時 `reason` 必附具體原因（例如
 * `"observed_tps missing"`、`"snapshot missing"`）——UI 顯示「無法
 * 比較：{reason}」，不留白補 0。 */
export interface PeerComparisonEntry {
  asset_id: string
  snapshot: PeerMetricsSnapshot | null
  comparable: boolean
  reason: string | null
}

/** `GET /api/peer-metrics?asset=` 回應資料——查無此資產時 `snapshot` 為
 * `null`、`peers` 為空陣列（語意是「查無資料」而非請求錯誤）。
 * `illustrative` 恆為 `true`：本端點資料只來自 fixture，非即時真實
 * 觀測值，UI 必須顯眼揭露（誠實鐵則，見 openapi.yaml 說明）。 */
export interface PeerMetricsResponseData {
  illustrative: true
  snapshot: PeerMetricsSnapshot | null
  peers: PeerComparisonEntry[]
}

// ── /api/eco-link ────────────────────────────────────────────────────────
// 對應後端 trustforge/ecolink.py enum 與 contract（#580）

export type DependencyKind =
  | 'bridge'
  | 'oracle'
  | 'liquidity'
  | 'settlement'
  | 'governance'
  | 'infrastructure'
  | 'unknown'

export type ImpactDirection =
  | 'positive'
  | 'negative'
  | 'mixed'
  | 'unknown'

export type UpgradeEventStatus =
  | 'announced'
  | 'scheduled'
  | 'activated'
  | 'cancelled'

export const DEPENDENCY_KINDS: readonly DependencyKind[] = [
  'bridge', 'oracle', 'liquidity', 'settlement', 'governance', 'infrastructure', 'unknown',
] as const

export const IMPACT_DIRECTIONS: readonly ImpactDirection[] = [
  'positive', 'negative', 'mixed', 'unknown',
] as const

export const UPGRADE_EVENT_STATUSES: readonly UpgradeEventStatus[] = [
  'announced', 'scheduled', 'activated', 'cancelled',
] as const

/** 一條依賴邊：source → target 的官方關係宣告（含有效期間）。 */
export interface DependencyEdge {
  schema_version: string
  source_asset_id: string
  target_asset_id: string
  kind: DependencyKind
  valid_from: string
  valid_until: string | null
  confidence: number
  official_source_url: string
  observed_at: string
}

/** 一則升級事件：區塊鏈官方升級／治理提案的結構化紀錄。 */
export interface UpgradeEvent {
  schema_version: string
  event_id: string
  asset_id: string
  title: string
  scheduled_at: string | null
  actual_at: string | null
  status: UpgradeEventStatus
  impact_direction: ImpactDirection
  impacted_asset_ids: string[]
  official_source_url: string
  observed_at: string
}

/** 一條影響路徑：官方升級事件與依賴邊之間的*相關性*（非因果）。 */
export interface EcoLinkImpactPath {
  event_id: string
  path: string[]
  direction: string
  confidence: number
  official_source_url: string
}

/** `GET /api/eco-link?asset=` 回應資料。`verdict === 'insufficient_data'`
 * 時 `impact_paths` 為空陣列，UI 顯示「資料不足，無法判定」，不假裝
 * 「沒有影響」（見 openapi.yaml eco-link 說明）。措辭一律「可能相關」，
 * 前端不得出現「導致」「因此」等因果字眼。`illustrative` 恆為 `true`：
 * 本端點資料只來自 fixture，非即時真實抓取，UI 必須顯眼揭露。 */
export interface EcoLinkResponseData {
  illustrative: true
  verdict: 'possible_relation' | 'insufficient_data'
  message: string
  impact_paths: EcoLinkImpactPath[]
}

/** 洞察的一個貢獻來源（InsightExplainabilityPanel 最小單元）。 */
export interface InsightContribution {
  source: string
  kind: string
  claim_id?: string | null
  text: string
  /** 該貢獻提供的方向性信號：bullish / bearish / neutral。 */
  direction: string
  trust: number
}

/** 一條可驗證、非顯而易見的獨特洞察（見 `trust/insights.py`）。 */
export interface Insight {
  insight_type: string
  title: string
  summary: string
  /** 整體淨方向：bullish / bearish / neutral / ambiguous。 */
  direction: string
  /** 0–1 誠實強度；覆蓋不足時固定 0。 */
  strength: number
  /** "covered" | "insufficient"（見誠實覆蓋閘）。 */
  coverage: string
  coverage_reason: string
  contributions: InsightContribution[]
  claim_ids: string[]
  meta?: Record<string, unknown>
}

/** D1.5 假設驗證題型結構化正反方帳本：顯式 pro/con 證據綁定 Evidence List
 * （`pro`/`con` 為 evidence 陣列索引，對應前端證據清單的 E{i}），並附資訊完整度限制
 * 聲明（不過度宣稱預測力）。僅 `question_type === "hypothesis"` 時由後端填入。 */
export interface HypothesisLedger {
  pro: number[]
  con: number[]
  confidence_limit: string
}

export interface TrustRadarDimension {
  label: string
  has_data: boolean
  trust: number | null
  n_sources: number
  n_evidence: number
  single_source: boolean | null
}

/** key 為維度代號（price/onchain/regulatory/hoyabit/news/social/... ）。 */
export type TrustRadar = Record<string, TrustRadarDimension>

/** #106 D0.4 三態誠實合約：每個分項都是 `number | null`——`null` 表示「本輪
 * 完全沒有該分項的可信資料」，**不等於 0**。後端 `_aggregate_trust_components`
 * 永遠回傳 4 個鍵（結構穩定、便於 validator 與渲染），某分項未評估時其值為
 * `null`（絕不補 0）。前端消費端（含 `TrustBreakdown`）遇到 `null` 必須顯式
 * 渲染「暫無評分」中性態，不得補 0 冒充「評了但零分／風險極低」。 */
export interface TrustComponentsAggregate {
  reputation: number | null
  corroboration: number | null
  recency: number | null
  manipulation: number | null
}

export interface PriceProvenanceEntry {
  content_reference: string
  fetched_at: string
  source_url: string
  data_lineage?: DataLineage | null
}

/** key 目前觀察到如 "ohlcv"；後端未固定列舉，保留 string 索引。 */
export type PriceProvenance = Record<string, PriceProvenanceEntry>

export interface AnalyzeData {
  version: string
  report: Report
  evidence: Evidence[]
  trust_radar: TrustRadar
  trust_components_aggregate: TrustComponentsAggregate
  price_provenance: PriceProvenance
  /** Hermes workflow envelope. Optional while old cached responses age out. */
  execution?: ExecutionManifest
  execution_log: ExecutionEvent[]
}

export interface ExecutionManifest {
  agent: 'hermes'
  run_id: string
  started_at: string
  elapsed_sec: number
  budget_sec: number
  nodes: Array<{ id: string; label: string; order: number }>
}

export interface ExecutionEvent {
  ts: string
  elapsed_sec: number
  tool: string
  params: Record<string, unknown>
  summary: string
}

/** `/api/analyze?type=comparison` 回傳的結構化比較報告（CA-06）。對應
 * `ComparisonReport.to_dict()`——四個比較面向（DimensionResult）、綜合結論、
 * 限制與可能推翻結論的條件。 */
export interface ComparisonReportData {
  coin_a: string
  coin_b: string
  query: string
  conclusion: string
  dimensions: Array<{
    dimension: string
    decision: string
    reasoning: string
    confidence: number
    evidence_refs: string[]
    abstain_reason?: string
  }>
  confidence: number
  limits: string[]
  could_flip: string[]
  generated_at: string
  supporting_report_a?: Report
  supporting_report_b?: Report
  supporting_evidence_a?: Evidence[]
  supporting_evidence_b?: Evidence[]
}

/** `/api/analyze?type=comparison`：`report`/`evidence`/... 全套欄位各出現
 * 兩次（`_a`/`_b` 後綴），對應 `_handle_api_analyze` comparison 分支 payload
 * （見 `web.py`）。刻意不重用 `AnalyzeData`（欄位名不同、非巢狀關係），單獨
 * 定義一份型別，兩邊各自獨立、互不影響。 */
export interface ComparisonAnalyzeData {
  version: string
  report_a: Report
  evidence_a: Evidence[]
  trust_radar_a: TrustRadar
  trust_components_aggregate_a: TrustComponentsAggregate
  price_provenance_a: PriceProvenance
  report_b: Report
  evidence_b: Evidence[]
  trust_radar_b: TrustRadar
  trust_components_aggregate_b: TrustComponentsAggregate
  price_provenance_b: PriceProvenance
  execution?: ExecutionManifest
  execution_log: ExecutionEvent[]
  /** CA-06：結構化比較報告（`ComparisonReport.to_dict()`）；需要 `build_comparison_report`
   *  的規則層介入，不一定每次都有（離線／樣本例可能為 null）。 */
  comparison_report?: ComparisonReportData | null
}

// ── /api/health ──────────────────────────────────────────────────────────

export interface HealthData {
  status: string
  version: string
  uptime_seconds: number
}

// ── /api/status ──────────────────────────────────────────────────────────

/** 對應 `get_freshness_snapshot()`：`status==="missing"` 時 `fetched_at`／
 * `age_seconds` 皆為 `null`（見 `ingestion/cache.py` docstring）。 */
export interface FreshnessEntry {
  source: string
  coin: string
  status: 'fresh' | 'stale' | 'missing'
  fetched_at: number | null
  age_seconds: number | null
}

export interface CacheBackendStatus {
  name: string
  connected: boolean
  primary_connected: boolean
  active_backend: string
  degraded: boolean
}

export interface StatusData {
  version: string
  uptime_seconds: number
  bedrock_capable: boolean
  live_token_set: boolean
  cache_backend: CacheBackendStatus
  freshness: {
    fresh: number
    stale: number
    missing: number
    entries: FreshnessEntry[]
  }
}

// ── /api/costs ───────────────────────────────────────────────────────────

export interface CostModelDetail {
  cost_usd: number
  tokens_in: number
  tokens_out: number
}

/** 單筆帳本 run 紀錄（`ledger.append_run()` 寫入時的欄位）。`calls` 只用來算
 * 呼叫數，不逐筆驗證內容。 */
export interface LedgerRunRecord {
  ts: string
  coin?: string | null
  question_type?: string
  offline?: boolean
  total_cost_usd: number
  calls: unknown[]
}

/** `Ledger.summary()`（`ledger.py`）——codex HIGH（成本端點可擴展性）修復後
 * 為有界摘要：`run_count` 是帳本真實總筆數，`runs` 只回最近 N 筆（後端
 * `SUMMARY_RECENT_RUNS_CAP`，目前 50），不是無界成長的完整清單。UI 顯示
 * 「總筆數」一律讀 `run_count`，`runs` 只用來畫「最近 N 筆」明細表。 */
export interface CostsData {
  total_cost_usd: number
  by_model: Record<string, number>
  by_model_detail: Record<string, CostModelDetail>
  run_count: number
  runs: LedgerRunRecord[]
  offset?: number
  limit?: number
}

// ── /api/carbon ──────────────────────────────────────────────────────────

/** 碳足跡估算回應（所有數值均為 ESTIMATES）。 */
export interface CarbonData {
  total_tokens: number
  total_estimated_kwh: number
  total_estimated_co2e_g: number
  total_estimated_co2e_kg: number
  call_count: number
  breakdown_by_model: Record<string, { tokens: number; kwh: number; co2e_g: number; calls: number }>
  methodology: string
  is_estimate: boolean
  disclaimer: string
}

// ── /api/history ─────────────────────────────────────────────────────────

/** 對應 `scripts/fetch_scheduler.py::_snapshot_dict()` 再補上
 * `get_trust_history()` 加的 `"date"` 欄位。`reputation_trace` 選填理由
 * 同 `OverviewCoin`。 */
export interface TrustHistoryEntry {
  date: string
  coin: string
  trust_score: number
  direction: string
  calibrated_confidence: number
  decision_state: DecisionState
  generated_at: string
  reputation_trace?: Record<string, ReputationTraceEntry>
}

export interface HistoryData {
  coin: string
  days: number
  history: TrustHistoryEntry[]
}

// ── /api/admin/* ────────────────────────────────────────────────────────────
// 對應 `web.py::_admin_config_view()`（GET/PUT 共用）與
// `_handle_api_admin_audit()`；完整契約見 docs/api/openapi.yaml admin tag。
// token 相關欄位只有 configured bool + 末 4 碼——後端絕不回明文/hash，
// 前端型別層面也不允許出現這種欄位。

/** `daily_cap_usd` 三層對照：config（管理面寫入）/ env（原始字串，可能是
 * 壞值，如實顯示）/ default（$3.0），`effective`/`source` 是分析路徑當下
 * 真正生效的值與層級（後端直接取自 budget_guard 同一批函式，非重算）。
 * `source` 保持寬字串（enum 可能演進，如 env kill-switch 分支），渲染端
 * 只做徽章顯示、不做窮舉比對。 */
export interface AdminCapView {
  config: number | null
  env: string | null
  default: number
  effective: number
  source: string
}

export interface AdminBedrockView {
  config: boolean | null
  /** `BEDROCK_MODEL_ID` env 是否已設（開關與 model id 是 AND 關係，只回 bool）。 */
  bedrock_model_id_set: boolean
  /** live 閘當下實際開閉（AND 後結果）。 */
  effective: boolean
  source: string
}

export interface AdminBooleanSwitchView {
  config: boolean | null
  env: string | null
  default: boolean
  effective: boolean
  source: string
}

export interface AdminLiveTokenView {
  config_configured: boolean
  /** 末 4 碼；token 過短時後端回 null（避免 last4 洩露過半明文）。 */
  config_last4: string | null
  env_configured: boolean
  effective_configured: boolean
  source: string
}

export interface AdminConfigData {
  daily_cap_usd: AdminCapView
  bedrock_enabled: AdminBedrockView
  multi_angle_narration_enabled: AdminBooleanSwitchView
  hermes_autonomy_enabled: {
    config: boolean | null
    env: string | null
    effective: boolean
    source: string
  }
  live_token: AdminLiveTokenView
  /** CAS 樂觀鎖版本；item 不存在（`exists=false`）時為 null，PUT 傳 0。 */
  version: number | null
  updated_at: string | null
  updated_by: string | null
  exists: boolean
  version_corrupt: boolean
  /** 只在 PUT 200 回應出現（如「BEDROCK_MODEL_ID 未設定…」誠實警告、
   * 審計側路寫入失敗的 best-effort 警告）。 */
  warnings?: string[]
}

/** PUT body 的部分更新欄位（`expected_version` 由 endpoints 層自動附上）。
 * 值 `null`＝清除該 config 層欄位（回落 env/default）。 */
export interface AdminConfigChanges {
  daily_cap_usd?: number | null
  bedrock_enabled?: boolean | null
  multi_angle_narration_enabled?: boolean | null
  hermes_autonomy_enabled?: boolean | null
  live_token?: string | null
}

export interface WhaleAlertCredentialStatus {
  configured: boolean
  source: 'ssm' | 'file' | 'environment' | 'unconfigured' | 'unavailable'
  last_verified_at: string | null
}

export type CmcCredentialStatus = WhaleAlertCredentialStatus

export type BackendProviderKey =
  | 'memory'
  | 'policy'
  | 'eval'
  | 'llm'
  | 'gateway'
  | 'observability'
  | 'upgrade'

export type BackendProvider = 'builtin' | 'agentcore'

export interface AdminBackendProvidersData {
  kind: 'backend_provider_registry'
  providers: Record<BackendProviderKey, BackendProvider>
  valid_providers: BackendProvider[]
  provider_keys: BackendProviderKey[]
  hot_config: boolean
  restart_required: boolean
}

export interface AdminAuditChange {
  field: string
  /** token 類欄位 old/new 是後端遮罩值（`"<set>"`/`"<cleared>"`/
   * `"<rotated last4=xxxx>"`），絕無明文——前端顯示層再轉成「已輪替」等
   * 中文字樣（見 `adminConsole.ts::formatAuditValue`）。 */
  old?: unknown
  new?: unknown
}

export interface AdminAuditRecord {
  ts: string | null
  actor: string | null
  changes: AdminAuditChange[]
  version_from: number | null
  version_to: number | null
  user_agent: string | null
}

export interface AdminAuditData {
  limit: number
  records: AdminAuditRecord[]
}
