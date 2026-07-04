// TrustForge API 型別定義——對應後端 `src/trustforge/schema.py` 與
// `src/trustforge/web.py` `/api/*` 各端點回傳結構（見
// docs/PLAN-frontend-backend-split.md §2）。所有型別皆為唯讀資料模型，不含
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
    retry_href?: string
  }
}

export type DecisionState = 'abstain' | 'low_confidence' | 'normal'

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
  trust: number
  trust_components: Record<string, number>
  /** 確定判定為操縱的紅旗，已反映在 trust 分數。 */
  flags: string[]
  /** W3：中性資訊提示（如高相似度叢集），不代表操縱判定、不影響 trust。 */
  info_flags: string[]
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
  calibrated_confidence: number
  decision_state: DecisionState
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

export interface TrustComponentsAggregate {
  reputation: number
  corroboration: number
  recency: number
  manipulation: number
}

export interface PriceProvenanceEntry {
  content_reference: string
  fetched_at: string
  source_url: string
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
  execution_log: unknown[]
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
  execution_log: unknown[]
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
  coin?: string
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
