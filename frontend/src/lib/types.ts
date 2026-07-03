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
  stance_pairs?: StancePair[]
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

// ── /api/health ──────────────────────────────────────────────────────────

export interface HealthData {
  status: string
  version: string
  uptime_seconds: number
}
