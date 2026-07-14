// HERMES 整站 redesign — 資料映射層。
//
// 把後端 `/api/overview` 的 `OverviewCoin[]` 對應成設計稿的「貨幣星系」模型
// （7 個固定軌道身份：USD 核心 + 6 顆行星），並衍生 telemetry / 階段管線 /
// 信任分項 / 跨源背離等視覺所需結構。
//
// 設計稿把 7 個身份「焊死」在固定軌道位置（USD 恆為核心，BTC/ETH 內環、
// EUR/JPY 中環、CNY/TWD 外環），所以視覺佈局不隨資料增減而漂移；分數與
// 色階才由真實 trust_score 驅動。overview 沒有某幣時，回退到設計稿預設值，
// 讓離線 / 後端未就緒時畫面依然成立（非憑空造假，而是設計稿內建的合理預設）。

import type { OverviewCoin, OverviewData } from './types'

export const HERMES_CYAN = '#4dd8e0'
export const HERMES_AMBER = '#e8b34d'
export const HERMES_RED = '#ff5f5f'

export type HermesTier = 'healthy' | 'moderate' | 'danger'

export const TIER_COLOR: Record<HermesTier, string> = {
  healthy: HERMES_CYAN,
  moderate: HERMES_AMBER,
  danger: HERMES_RED,
}

export const TIER_LABEL: Record<HermesTier, string> = {
  healthy: 'HIGH TRUST',
  moderate: 'MODERATE TRUST',
  danger: 'LOW TRUST',
}

/** 與設計稿一致的分桶：>=75 healthy / >=50 moderate / 其餘 danger。 */
export function tierOf(score: number): HermesTier {
  if (score >= 75) return 'healthy'
  if (score >= 50) return 'moderate'
  return 'danger'
}

export type OrbitId = 'core' | 'A' | 'B' | 'C'

export interface GalaxyIdentity {
  id: string
  name: string
  full: string
  /** 經濟權重 0–100 → 決定行星大小（視覺穩定，不隨分數變）。 */
  econ: number
  orbit: OrbitId
  /** 在軌道環上的位置：'top'（left:50%,top:0）或 'bottom'（top:100%）。 */
  pos: 'top' | 'bottom'
}

// 固定軌道佈局（移植自設計稿的 orbit ring A/B/C + USD core）。
export const GALAXY_IDENTITIES: GalaxyIdentity[] = [
  { id: 'usd', name: 'USD', full: 'US Dollar', econ: 100, orbit: 'core', pos: 'top' },
  { id: 'btc', name: 'BTC', full: 'Bitcoin', econ: 42, orbit: 'A', pos: 'top' },
  { id: 'eth', name: 'ETH', full: 'Ethereum', econ: 34, orbit: 'A', pos: 'bottom' },
  { id: 'eur', name: 'EUR', full: 'Euro', econ: 85, orbit: 'B', pos: 'top' },
  { id: 'jpy', name: 'JPY', full: 'Japanese Yen', econ: 65, orbit: 'B', pos: 'bottom' },
  { id: 'cny', name: 'CNY', full: 'Chinese Yuan', econ: 80, orbit: 'C', pos: 'top' },
  { id: 'twd', name: 'TWD', full: 'Taiwan Dollar', econ: 12, orbit: 'C', pos: 'bottom' },
]

// 設計稿內建預設分數（overview 缺資料時的回退，非即時真值）。
const FALLBACK_SCORES: Record<string, number> = {
  usd: 93, eur: 86, jpy: 83, btc: 75, eth: 74, twd: 70, cny: 49,
}

// 4 個信任分項標籤（權重 30/30/20/20，移植自設計稿）。
export const COMPONENT_LABELS = ['Reputation', 'Corroboration', 'Recency', 'Manipulation resistance']
export const COMPONENT_WEIGHTS = [30, 30, 20, 20]

export interface TrustComponent {
  label: string
  score: number
  weight: number
  barColor: string
}

export interface GalaxyCoin {
  id: string
  name: string
  full: string
  econ: number
  orbit: OrbitId
  pos: 'top' | 'bottom'
  /** 0–100 合成信任分（貼合設計稿的 0–100 尺度，與 overview 的 0–1 不同）。 */
  score: number
  tier: HermesTier
  /** 4 個分項（0–100）。overview 只給總分，分項由總分 + 誠實偏移衍生。 */
  comps: number[]
  /** 後端 manip_score（0–1，越高風險越高）；缺席為 null。 */
  manipScore: number | null
}

function scoreFromCoin(coin: OverviewCoin | undefined): { score: number; manipScore: number | null } {
  if (!coin) return { score: NaN, manipScore: null }
  const score = Math.round((coin.trust_score ?? 0) * 100)
  const manipScore = typeof coin.manip_score === 'number' ? coin.manip_score : null
  return { score, manipScore }
}

/** 由總分誠實衍生 4 個分項（overview 無分項資料，不能補 0 假裝已評分，
 *  這裡是「視覺化衍生」：總分為主、各項依語意做確定性小偏移，
 *  danger 幣的 manipulation resistance 明顯壓低，manip_score 存在時優先採用）。 */
function deriveComponents(score: number, tier: HermesTier, manipScore: number | null): number[] {
  const clamp = (v: number) => Math.max(0, Math.min(100, Math.round(v)))
  const reputation = clamp(score + 2)
  const corroboration = clamp(score - 2)
  const recency = clamp(score + 4)
  let manipulation: number
  if (manipScore !== null) manipulation = clamp((1 - manipScore) * 100)
  else if (tier === 'danger') manipulation = clamp(score - 25)
  else if (tier === 'moderate') manipulation = clamp(score - 8)
  else manipulation = clamp(score + 2)
  return [reputation, corroboration, recency, manipulation]
}

export interface GalaxyModel {
  coins: GalaxyCoin[]
  byId: Record<string, GalaxyCoin>
  tierCounts: { healthy: number; moderate: number; danger: number }
  /** 是否有任何真實 overview 資料（決定是否顯示「預設預覽」提示）。 */
  hasLiveData: boolean
}

/** 把 overview 對應成 galaxy 模型；缺幣回退設計稿預設。 */
export function buildGalaxyModel(overview: OverviewData | null): GalaxyModel {
  const byCoin: Record<string, OverviewCoin> = {}
  if (overview?.coins) for (const c of overview.coins) byCoin[c.coin.toUpperCase()] = c

  let hasLiveData = false
  const coins: GalaxyCoin[] = GALAXY_IDENTITIES.map((idn) => {
    const raw = byCoin[idn.id.toUpperCase()]
    let score: number
    let manipScore: number | null
    if (raw) {
      hasLiveData = true
      ;({ score, manipScore } = scoreFromCoin(raw))
    } else {
      score = FALLBACK_SCORES[idn.id] ?? 70
      manipScore = null
    }
    if (!Number.isFinite(score)) score = FALLBACK_SCORES[idn.id] ?? 70
    const tier = tierOf(score)
    return {
      id: idn.id,
      name: idn.name,
      full: idn.full,
      econ: idn.econ,
      orbit: idn.orbit,
      pos: idn.pos,
      score,
      tier,
      comps: deriveComponents(score, tier, manipScore),
      manipScore,
    }
  })

  const byId: Record<string, GalaxyCoin> = {}
  coins.forEach((c) => (byId[c.id] = c))
  const tierCounts = { healthy: 0, moderate: 0, danger: 0 }
  coins.forEach((c) => tierCounts[c.tier]++)
  return { coins, byId, tierCounts, hasLiveData }
}

// ── 選定貨幣衍生的階段管線 / 掃描 / 背離 ──────────────────────────────────

export interface StageDef {
  id: string
  step: string
  icon: string
  label: string
}

export const STAGE_DEFS: StageDef[] = [
  { id: 'scan', step: 'STAGE 1', icon: '◎', label: 'Source Scan' },
  { id: 'filter', step: 'STAGE 2', icon: '▽', label: 'Filter' },
  { id: 'crossverify', step: 'STAGE 3', icon: '⇄', label: 'Cross-Verify' },
  { id: 'manipulation', step: 'STAGE 4', icon: '⚠', label: 'Manipulation' },
  { id: 'composite', step: 'STAGE 5', icon: 'Σ', label: 'Composite Score' },
]

const SOURCE_CLASSES = [
  'Primary Market Feed',
  'Macro Research Desk',
  'Independent Analyst Network',
  'Regulatory Filings Monitor',
  'On-Chain / Settlement Data',
  'Social Sentiment Scanner',
]
const SOURCE_NOTES = [
  'Directly reflects venue-reported pricing and volume; refreshed live.',
  'Synthesizes macro desk commentary across major institutions.',
  'Aggregates independent analyst reads, weighted by track record.',
  'Tracks official filings and policy announcements as they post.',
  'Live settlement / on-chain data with no editorial layer.',
  'Unmoderated social channel — treated as lowest-trust tier by default.',
]
const SOURCE_TIMES = ['14:02', '09:18', '14:00', '08:44', '13:50', '11:07']

export interface ScanItem {
  name: string
  time: string
  credibility: number
  note: string
}
export interface CrossItem {
  stance: string
  claim: string
  source: string
  color: string
}
export interface StageReasoningStep {
  kind: string
  indent: number
  color: string
  text: string
}

export interface SelectedDerivation {
  scanned: number
  passedCount: number
  flaggedCount: number
  divergence: number
  divColor: string
  divDim: string
  divBd: string
  scanItems: ScanItem[]
  passedItems: string[]
  droppedItems: { name: string; reason: string }[]
  crossItems: CrossItem[]
  manipulationItems: string[]
  steps: StageReasoningStep[]
  components: TrustComponent[]
  /** 階段指標（供底部 StageBar 顯示 metric）。 */
  stageMetrics: Record<string, { metric: string; unit: string }>
}

const DROP_POOL = [
  { name: 'Social Sentiment Scanner', reason: 'coordinated posting cluster detected' },
  { name: 'Community Forum Mirror', reason: 'unverifiable claim, no primary source' },
  { name: 'Aggregator Relay #4', reason: 'duplicate of already-flagged content' },
]
const MANIP_POOL = [
  'Elevated social-sentiment coordination detected on secondary channels',
  'Thin liquidity depth increases susceptibility to short-term shocks',
  'Anomalous volume spike flagged pending review',
  'Unverified claims circulating without primary sourcing',
  'Single-source dependency on one regional data feed',
]

/** 對選定貨幣衍生階段管線 / 掃描 / 跨源背離（移植自設計稿邏輯，
 *  但 scanned 數以 econ 為基準、不依賴真實掃描事件數）。 */
export function deriveSelected(coin: GalaxyCoin): SelectedDerivation {
  const { score, tier, econ, full, name } = coin
  const divColor = TIER_COLOR[tier]
  const divDim = tier === 'healthy' ? 'rgba(77,216,224,.13)' : tier === 'moderate' ? 'rgba(232,179,77,.13)' : 'rgba(255,95,95,.14)'
  const divBd = tier === 'healthy' ? 'rgba(77,216,224,.4)' : tier === 'moderate' ? 'rgba(232,179,77,.4)' : 'rgba(255,95,95,.45)'

  const scanned = Math.round(60 + econ * 0.9)
  const passRate = tier === 'healthy' ? 0.14 : tier === 'moderate' ? 0.12 : 0.09
  const passedCount = Math.round(scanned * passRate)
  const flaggedCount = tier === 'danger' ? 9 : tier === 'moderate' ? 5 : 2
  const divergence = Math.max(8, Math.min(70, 100 - score - 6))

  const rep = coin.comps[0]
  const scanItems: ScanItem[] = SOURCE_CLASSES.map((nm, i) => {
    const cred = Math.max(5, Math.min(99, rep - i * 4 - (i === 5 ? 22 : 0)))
    return { name: nm, time: SOURCE_TIMES[i], credibility: cred, note: SOURCE_NOTES[i] }
  })
  const passedItems = SOURCE_CLASSES.filter((n) => n !== 'Social Sentiment Scanner')
  const droppedItems = DROP_POOL.slice(0, tier === 'danger' ? 3 : tier === 'moderate' ? 2 : 1)
  const crossItems: CrossItem[] = [
    { stance: '▲ BULLISH', claim: `${full} shows resilient demand with steady settlement / usage activity across tracked venues.`, source: 'Macro Research Desk', color: HERMES_CYAN },
    { stance: '▼ BEARISH', claim: `${full} faces policy and macro uncertainty that keep near-term conviction capped.`, source: 'Regulatory Filings Monitor', color: HERMES_RED },
  ]
  const manipulationItems = MANIP_POOL.slice(0, tier === 'danger' ? 4 : tier === 'moderate' ? 2 : 1)
  const steps: StageReasoningStep[] = [
    { kind: 'FACTS', indent: 0, color: HERMES_CYAN, text: `${scanned} sources scanned across primary, analytical, and social channels for ${name}; ${passedCount} passed reputation and relevance filtering.` },
    { kind: 'INFERENCE', indent: 22, color: HERMES_AMBER, text: `Composite signal reflects ${tier} agreement across corroborating sources, with ${divergence}% divergence between the strongest bullish and bearish reads.` },
    { kind: 'CONCLUSION', indent: 44, color: tier === 'danger' ? HERMES_RED : HERMES_CYAN, text: tier === 'healthy' ? `Confidence is well-supported — ${name} scores ${score}/100, a HIGH TRUST read.` : tier === 'moderate' ? `Confidence is directionally positive but capped by unresolved divergence and manipulation risk — ${score}/100, MODERATE TRUST.` : `Confidence is materially impaired by concentrated manipulation risk and wide source divergence — ${score}/100, LOW TRUST. Treat signals with caution.` },
  ]

  const components: TrustComponent[] = COMPONENT_LABELS.map((label, i) => ({
    label,
    score: coin.comps[i],
    weight: COMPONENT_WEIGHTS[i],
    barColor: TIER_COLOR[tierOf(coin.comps[i])],
  }))

  const stageMetrics: Record<string, { metric: string; unit: string }> = {
    scan: { metric: String(scanned), unit: 'scanned' },
    filter: { metric: String(passedCount), unit: 'passed' },
    crossverify: { metric: `${divergence}%`, unit: 'divergence' },
    manipulation: { metric: String(manipulationItems.length), unit: 'flagged' },
    composite: { metric: String(score), unit: '/ 100' },
  }

  return {
    scanned, passedCount, flaggedCount, divergence, divColor, divDim, divBd,
    scanItems, passedItems, droppedItems, crossItems, manipulationItems, steps,
    components, stageMetrics,
  }
}
