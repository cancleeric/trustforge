// oxlint-disable react/only-export-components
import { useHermesI18n, type HermesLocale, type MessageKey } from '../hermes/hermesI18n'

const DIMENSION_NAMES = [
  'issuance_predictability',
  'control_dispersion',
  'supply_verifiability',
  'governance_capture_resistance',
  'holder_concentration',
] as const
const CANONICAL_WEIGHT = 0.032
const CANONICAL_TOTAL_CAP = 0.08
const CANONICAL_REQUIRED_KNOWN = 3
const CANONICAL_REQUIRED_FAMILIES = 2
const MAX_TEXT_LENGTH = 4096
const MAX_REVISION_LENGTH = 256
const MAX_URL_LENGTH = 2048
const MAX_URL_COUNT = 16

type DimensionName = typeof DIMENSION_NAMES[number]
type DimensionStatus = 'known' | 'unknown' | 'stale' | 'conflicted'

interface IntrinsicProvenance {
  source_urls: string[]
  source_revision: string
  content_hash: string
  evidence_kind: string
  source_coordinates: string
  as_of: string
  fetched_at: string
}

interface IntrinsicDimension {
  name: DimensionName
  status: DimensionStatus
  raw: number | null
  normalized: number | null
  weight: number
  signed_delta: number
  reason_code: string
  coverage: string
  provenance: IntrinsicProvenance | null
}

interface IntrinsicGate {
  passed: boolean
  known_count: number
  required_known: number
  source_family_count: number
  required_source_families: number
  reason_code: string
}

// #878: official promotion receipt.  The backend does not yet emit this struct
// (it is not injected into the report payload); the parser validates it strictly
// so a future wired receipt cannot silently upgrade a shadow into official.
// Field set is the one named in the issue: receipt_id / policy_digest / decision
// / reasons / calibration_claim.  Internal sub-schemas (e.g. calibration_claim)
// are pending backend wiring and are only required to be present objects here.
interface IntrinsicPromotionReceipt {
  receipt_id: string
  policy_digest: string
  decision: 'pass'
  reasons: string[]
  calibration_claim: Record<string, unknown>
}

interface IntrinsicReleaseCapability {
  capability: 'asset_intrinsic'
  promoted_at: string
}

interface IntrinsicAssessment {
  schema_version: '1.0.0'
  mode: 'shadow' | 'official'
  affects_official_score: boolean
  asset_id: string
  as_of: string
  total_delta: number
  total_delta_cap: number
  conflict_detected: boolean
  gate: IntrinsicGate
  dimensions: IntrinsicDimension[]
  release_capability?: IntrinsicReleaseCapability
  promotion_receipt?: IntrinsicPromotionReceipt
}

const copy = {
  'zh-TW': {
    title: '資產內在事實觀察',
    badge: 'SHADOW／不影響正式信任分',
    description: '這是獨立觀察值，只用來驗證方法；不會改動信任分、方向或市場判斷。',
    officialBadge: 'OFFICIAL／已納入正式信任分',
    officialDescription: '這項資產結構調整已記錄發行能力與 promotion receipt；它只調整信任分，不直接改變市場方向。',
    receipt: 'Promotion receipt',
    gatePassed: '覆蓋閘已通過',
    gateFailed: '覆蓋不足，所有調整維持 0',
    known: '已知維度',
    families: '來源家族',
    delta: 'Shadow delta',
    asOf: 'PIT 截止',
    unavailable: '資產結構資料格式不相容，已停止顯示；正式信任分不受影響。',
    knownStatus: '已驗證',
    unknownStatus: '未知',
    staleStatus: '過時',
    conflictedStatus: '衝突',
    provenance: '證據溯源',
    source: '來源',
    revision: '版本',
    coordinates: '定位',
    coverage: '覆蓋說明',
    noProvenance: '目前沒有可顯示的 PIT 證據。',
  },
  en: {
    title: 'Asset intrinsic facts',
    badge: 'SHADOW / does not affect official trust score',
    description: 'This independent observation validates the method only. It cannot change the trust score, direction, or market judgment.',
    officialBadge: 'OFFICIAL / included in official trust score',
    officialDescription: 'This asset-structure adjustment records a release capability and promotion receipt. It adjusts trust only and does not directly set market direction.',
    receipt: 'Promotion receipt',
    gatePassed: 'Coverage gate passed',
    gateFailed: 'Insufficient coverage; every adjustment remains zero',
    known: 'Known dimensions',
    families: 'Source families',
    delta: 'Shadow delta',
    asOf: 'PIT cutoff',
    unavailable: 'The Asset Structure payload is incompatible and was not displayed. The official trust score is unaffected.',
    knownStatus: 'Verified',
    unknownStatus: 'Unknown',
    staleStatus: 'Stale',
    conflictedStatus: 'Conflicted',
    provenance: 'Evidence provenance',
    source: 'Source',
    revision: 'Revision',
    coordinates: 'Coordinates',
    coverage: 'Coverage',
    noProvenance: 'No displayable PIT evidence is available.',
  },
} satisfies Record<HermesLocale, Record<string, string>>

const dimensionLabels: Record<HermesLocale, Record<DimensionName, string>> = {
  'zh-TW': {
    issuance_predictability: '發行可預測性',
    control_dispersion: '控制權分散',
    supply_verifiability: '供給可驗證性',
    governance_capture_resistance: '治理抗俘獲',
    holder_concentration: '持幣集中度',
  },
  en: {
    issuance_predictability: 'Issuance predictability',
    control_dispersion: 'Control dispersion',
    supply_verifiability: 'Supply verifiability',
    governance_capture_resistance: 'Governance capture resistance',
    holder_concentration: 'Holder concentration',
  },
}

// #878: per-dimension "what this measures" copy lives in hermesI18n so the
// framing layer is never hardcoded in the component (AC5 / framing deliverable).
const DIMENSION_WHAT_KEYS: Record<DimensionName, MessageKey> = {
  issuance_predictability: 'intrinsicDimIssuanceWhat',
  control_dispersion: 'intrinsicDimControlWhat',
  supply_verifiability: 'intrinsicDimSupplyWhat',
  governance_capture_resistance: 'intrinsicDimGovernanceWhat',
  holder_concentration: 'intrinsicDimHolderWhat',
}

function stateExplanationKey(status: DimensionStatus): MessageKey {
  switch (status) {
    case 'known': return 'intrinsicStateKnown'
    case 'unknown': return 'intrinsicStateUnknown'
    case 'stale': return 'intrinsicStateStale'
    case 'conflicted': return 'intrinsicStateConflicted'
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && actual.every((key) => keys.includes(key))
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function nonNegativeInteger(value: unknown): value is number {
  return finite(value) && Number.isInteger(value) && value >= 0
}

function awareTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value))
}

function nonBlankBounded(value: unknown, max: number): value is string {
  return typeof value === 'string' && /\S/.test(value) && value.length <= max
}

function canonicalSourceFamily(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > MAX_URL_LENGTH) return null
  try {
    const authority = value.match(/^https:\/\/([^/?#]+)/)?.[1]
    if (!authority || authority.includes('@') || authority.includes(':') || authority.includes('%')) return null
    const parsed = new URL(value)
    if (
      parsed.protocol !== 'https:'
      || !parsed.hostname
      || parsed.username
      || parsed.password
      || parsed.port
      || !/^[a-zA-Z0-9.-]+$/.test(parsed.hostname)
    ) return null
    return parsed.hostname.replace(/\.$/, '').toLowerCase()
  } catch {
    return null
  }
}

function parseProvenance(value: unknown): IntrinsicProvenance | null | undefined {
  if (value === null) return null
  if (
    !object(value)
    || !exactKeys(value, ['source_urls', 'source_revision', 'content_hash', 'evidence_kind', 'source_coordinates', 'as_of', 'fetched_at'])
    || !Array.isArray(value.source_urls)
    || value.source_urls.length > MAX_URL_COUNT
    || !value.source_urls.every((url) => canonicalSourceFamily(url) !== null)
    || !nonBlankBounded(value.source_revision, MAX_REVISION_LENGTH)
    || !/^[0-9a-f]{64}$/.test(typeof value.content_hash === 'string' ? value.content_hash : '')
    || (value.evidence_kind !== 'upstream_excerpt' && value.evidence_kind !== 'decision_record')
    || !nonBlankBounded(value.source_coordinates, MAX_TEXT_LENGTH)
    || !awareTimestamp(value.as_of)
    || !awareTimestamp(value.fetched_at)
  ) return undefined
  return value as unknown as IntrinsicProvenance
}

function parseDimension(value: unknown): IntrinsicDimension | null {
  if (
    !object(value)
    || !exactKeys(value, ['name', 'status', 'raw', 'normalized', 'weight', 'signed_delta', 'reason_code', 'coverage', 'provenance'])
    || !DIMENSION_NAMES.includes(value.name as DimensionName)
  ) return null
  const status = value.status as DimensionStatus
  // Drift B fix: backend emits known/unknown/stale/conflicted; the parser must
  // accept all four instead of collapsing the whole panel on stale/conflicted.
  if (status !== 'known' && status !== 'unknown' && status !== 'stale' && status !== 'conflicted') return null
  if (
    value.weight !== CANONICAL_WEIGHT
    || !finite(value.signed_delta)
    || !nonBlankBounded(value.coverage, MAX_TEXT_LENGTH)
  ) return null
  if (value.raw !== null && !finite(value.raw)) return null
  if (value.normalized !== null && !finite(value.normalized)) return null
  const provenance = parseProvenance(value.provenance)
  if (provenance === undefined) return null

  if (status === 'known') {
    if (
      !finite(value.raw) || value.raw < 0 || value.raw > 1
      || !finite(value.normalized) || value.normalized < 0 || value.normalized > 1
      || provenance === null
      || provenance.source_urls.length < 1
      || provenance.evidence_kind !== 'upstream_excerpt'
      || (value.reason_code !== 'eligible' && value.reason_code !== 'coverage_gate_not_met')
      || value.raw !== value.normalized
    ) return null
  } else {
    // unknown / stale / conflicted carry no numeric value and zero delta.
    if (value.raw !== null || value.normalized !== null || value.signed_delta !== 0) return null
    if (status === 'unknown') {
      if (
        (value.reason_code === 'fact_unavailable' && provenance !== null)
        || (value.reason_code === 'fact_unknown' && provenance === null)
        || (provenance?.evidence_kind === 'upstream_excerpt' && provenance.source_urls.length < 1)
        || (value.reason_code !== 'fact_unavailable' && value.reason_code !== 'fact_unknown')
      ) return null
    } else if (status === 'stale') {
      // Backend emits stale only from an aged known fact, always attaching a
      // non-null provenance (_public_provenance). reason_code is fixed.
      if (value.reason_code !== 'stale' || provenance === null) return null
    } else {
      // conflicted: backend always attaches non-null provenance carrying the
      // divergent sources; reason_code is fixed.
      if (value.reason_code !== 'fact_conflicted' || provenance === null) return null
    }
  }
  return { ...value, status, provenance } as unknown as IntrinsicDimension
}

const SHARED_ASSESSMENT_KEYS = [
  'schema_version', 'mode', 'affects_official_score', 'asset_id', 'as_of',
  'total_delta', 'total_delta_cap', 'conflict_detected', 'gate', 'dimensions',
] as const

const RECEIPT_KEYS = ['receipt_id', 'policy_digest', 'decision', 'reasons', 'calibration_claim'] as const

// Validates the structural body shared by shadow and official modes: numbers,
// gate, dimensions, the conflict_detected cross-check, and the delta math.
function validateAssessmentBody(value: Record<string, unknown>): { dimensions: IntrinsicDimension[]; gate: IntrinsicGate } | null {
  if (!nonBlankBounded(value.asset_id, MAX_REVISION_LENGTH) || !awareTimestamp(value.as_of) || !finite(value.total_delta) || value.total_delta_cap !== CANONICAL_TOTAL_CAP) return null
  if (typeof value.conflict_detected !== 'boolean') return null
  if (
    !object(value.gate)
    || !exactKeys(value.gate, ['passed', 'known_count', 'required_known', 'source_family_count', 'required_source_families', 'reason_code'])
  ) return null
  const gate = value.gate
  if (typeof gate.passed !== 'boolean' || typeof gate.reason_code !== 'string') return null
  const counts = ['known_count', 'required_known', 'source_family_count', 'required_source_families'] as const
  if (!counts.every((key) => nonNegativeInteger(gate[key]))) return null
  const checkedGate = gate as unknown as IntrinsicGate
  if (
    checkedGate.required_known !== CANONICAL_REQUIRED_KNOWN
    || checkedGate.required_source_families !== CANONICAL_REQUIRED_FAMILIES
    || checkedGate.known_count > DIMENSION_NAMES.length
    || (checkedGate.reason_code !== 'eligible' && checkedGate.reason_code !== 'insufficient_coverage')
  ) return null
  if (!Array.isArray(value.dimensions) || value.dimensions.length !== DIMENSION_NAMES.length) return null
  const dimensions = value.dimensions.map(parseDimension)
  if (dimensions.some((item) => item === null)) return null
  if (new Set(dimensions.map((item) => item?.name)).size !== DIMENSION_NAMES.length) return null
  const validDimensions = dimensions as IntrinsicDimension[]
  // conflict_detected must equal "any dimension is conflicted" (backend invariant).
  if (value.conflict_detected !== validDimensions.some((item) => item.status === 'conflicted')) return null
  const assessmentEpoch = Date.parse(value.as_of)
  if (validDimensions.some((item) => item.provenance && (
    Date.parse(item.provenance.as_of) > assessmentEpoch
    || Date.parse(item.provenance.fetched_at) > assessmentEpoch
    || Date.parse(item.provenance.fetched_at) < Date.parse(item.provenance.as_of)
  ))) return null
  const knownCount = validDimensions.filter((item) => item.status === 'known').length
  const sourceFamilies = new Set(validDimensions.flatMap((item) =>
    item.status === 'known' && item.provenance
      ? item.provenance.source_urls.map(canonicalSourceFamily)
      : [],
  ))
  const expectedGate = checkedGate.known_count >= checkedGate.required_known
    && checkedGate.source_family_count >= checkedGate.required_source_families
  if (
    knownCount !== checkedGate.known_count
    || sourceFamilies.size !== checkedGate.source_family_count
    || checkedGate.passed !== expectedGate
    || checkedGate.reason_code !== (checkedGate.passed ? 'eligible' : 'insufficient_coverage')
  ) return null
  if (Math.abs(value.total_delta) > CANONICAL_TOTAL_CAP) return null
  if (!checkedGate.passed && (value.total_delta !== 0 || validDimensions.some((item) => item.signed_delta !== 0))) return null
  if (validDimensions.some((item) =>
    item.status === 'known'
    && (
      item.reason_code !== (checkedGate.passed ? 'eligible' : 'coverage_gate_not_met')
      || item.signed_delta !== (checkedGate.passed ? Number(((item.normalized! - 0.5) * CANONICAL_WEIGHT).toFixed(8)) : 0)
    ),
  )) return null
  const expectedTotal = Math.max(
    -CANONICAL_TOTAL_CAP,
    Math.min(CANONICAL_TOTAL_CAP, Number(validDimensions.reduce((sum, item) => sum + item.signed_delta, 0).toFixed(8))),
  )
  if (value.total_delta !== expectedTotal) return null
  return { dimensions: validDimensions, gate: checkedGate }
}

export function parseIntrinsicAssessment(value: unknown): IntrinsicAssessment | null {
  if (!object(value)) return null
  if (value.mode === 'shadow') {
    // Drift A fix: backend emits 10 top-level keys including conflict_detected;
    // exactKeys discipline is kept (an extra or missing key still fails closed).
    if (!exactKeys(value, SHARED_ASSESSMENT_KEYS)) return null
    if (value.schema_version !== '1.0.0' || value.affects_official_score !== false) return null
    const body = validateAssessmentBody(value)
    if (!body) return null
    return { ...value, mode: 'shadow', dimensions: body.dimensions, gate: body.gate } as IntrinsicAssessment
  }
  if (value.mode === 'official') {
    // #878 official skeleton: accept mode 'official' only when a complete
    // release_capability + promotion_receipt (decision 'pass') is attached.
    // The frontend never self-declares official; it only validates a receipt.
    if (!exactKeys(value, [...SHARED_ASSESSMENT_KEYS, 'release_capability', 'promotion_receipt'])) return null
    if (value.schema_version !== '1.0.0' || value.affects_official_score !== true) return null
    if (
      !object(value.release_capability)
      || !exactKeys(value.release_capability, ['capability', 'promoted_at'])
      || value.release_capability.capability !== 'asset_intrinsic'
      || !awareTimestamp(value.release_capability.promoted_at)
    ) return null
    const receipt = value.promotion_receipt
    if (!object(receipt) || !exactKeys(receipt, RECEIPT_KEYS)) return null
    if (!nonBlankBounded(receipt.receipt_id, MAX_REVISION_LENGTH)) return null
    if (!nonBlankBounded(receipt.policy_digest, MAX_TEXT_LENGTH)) return null
    if (receipt.decision !== 'pass') return null
    if (!Array.isArray(receipt.reasons) || !receipt.reasons.every((r: unknown) => nonBlankBounded(r, MAX_TEXT_LENGTH))) return null
    if (!object(receipt.calibration_claim)) return null
    const body = validateAssessmentBody(value)
    if (!body || !body.gate.passed) return null
    return { ...value, mode: 'official', dimensions: body.dimensions, gate: body.gate } as IntrinsicAssessment
  }
  return null
}

function signed(value: number): string {
  if (value === 0) return '0.000'
  return `${value > 0 ? '+' : ''}${value.toFixed(3)}`
}

function statusLabel(status: DimensionStatus, text: Record<string, string>): string {
  if (status === 'known') return text.knownStatus
  if (status === 'unknown') return text.unknownStatus
  if (status === 'stale') return text.staleStatus
  return text.conflictedStatus
}

function statusColor(status: DimensionStatus): string {
  if (status === 'known') return 'text-tf-good'
  if (status === 'conflicted') return 'text-tf-warn'
  return 'text-tf-muted'
}

export default function AssetIntrinsicShadowPanel({ value }: { value: unknown }) {
  const { locale, t } = useHermesI18n()
  if (value === undefined || value === null) return null
  const assessment = parseIntrinsicAssessment(value)
  const text = copy[locale]
  if (!assessment) {
    return <p role="status" className="rounded-lg border border-tf-warn bg-tf-card p-3 text-sm text-tf-text2">{text.unavailable}</p>
  }
  const official = assessment.mode === 'official'
  const headingId = `intrinsic-${assessment.mode}-${assessment.asset_id}`

  return (
    <section className="hermes-clip min-w-0 overflow-hidden border border-tf-border bg-tf-card p-4" aria-labelledby={headingId} data-intrinsic-mode={assessment.mode}>
      <p className="text-[11px] font-bold uppercase tracking-wide text-tf-link">{t('intrinsicEyebrow')}</p>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="text-sm font-semibold text-tf-text">{text.title}</h3>
        <span className={`rounded-full border px-2 py-1 text-[11px] font-bold ${official ? 'border-tf-good text-tf-good' : 'border-tf-warn text-tf-warn'}`}>
          {official ? text.officialBadge : text.badge}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-tf-text2">{official ? text.officialDescription : text.description}</p>
      <p className="mt-2 text-xs leading-5 text-tf-text2">{t('intrinsicIntro')}</p>
      {official && assessment.promotion_receipt && (
        <p className="mt-2 min-w-0 break-all rounded border border-tf-good/60 bg-tf-good/10 p-2 font-mono text-[11px] leading-5 text-tf-good">
          {text.receipt}: {assessment.promotion_receipt.receipt_id}
        </p>
      )}
      {assessment.conflict_detected && (
        <p className="mt-2 rounded border border-tf-warn/60 bg-tf-warn/10 p-2 text-xs leading-5 text-tf-warn">{t('intrinsicConflictNote')}</p>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="rounded border border-tf-border p-2"><span className="text-tf-muted">{text.known}</span><b className="mt-1 block tf-num">{assessment.gate.known_count}/{assessment.gate.required_known}</b></div>
        <div className="rounded border border-tf-border p-2"><span className="text-tf-muted">{text.families}</span><b className="mt-1 block tf-num">{assessment.gate.source_family_count}/{assessment.gate.required_source_families}</b></div>
        <div className="rounded border border-tf-border p-2"><span className="text-tf-muted">{text.delta}</span><b className="mt-1 block tf-num">{signed(assessment.total_delta)}</b></div>
        <div className="min-w-0 rounded border border-tf-border p-2"><span className="text-tf-muted">{text.asOf}</span><b className="mt-1 block break-all font-mono text-[11px]">{assessment.as_of}</b></div>
      </div>
      <p className={`mt-3 text-xs font-semibold ${assessment.gate.passed ? 'text-tf-good' : 'text-tf-warn'}`}>
        {assessment.gate.passed ? text.gatePassed : text.gateFailed}
      </p>
      <ul className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
        {assessment.dimensions.map((dimension) => (
          <li key={dimension.name} className="min-w-0 rounded border border-tf-border p-3 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <b className="text-tf-text">{dimensionLabels[locale][dimension.name]}</b>
              <span className={statusColor(dimension.status)}>
                {statusLabel(dimension.status, text)} · {signed(dimension.signed_delta)}
              </span>
            </div>
            <p className="mt-2 break-words text-tf-text2"><span className="text-tf-muted">{t('intrinsicWhatLabel')}: </span>{t(DIMENSION_WHAT_KEYS[dimension.name])}</p>
            <p className="mt-1 break-words text-tf-text2"><span className="text-tf-muted">{t('intrinsicStateLabel')}: </span>{t(stateExplanationKey(dimension.status))}</p>
            <p className="mt-2 break-words text-tf-text2"><span className="text-tf-muted">{text.coverage}: </span>{dimension.coverage}</p>
            <details className="mt-2 min-w-0">
              <summary className="cursor-pointer text-tf-link">{text.provenance}</summary>
              {dimension.provenance ? (
                <dl className="mt-2 grid min-w-0 gap-1 text-tf-text2">
                  <div className="min-w-0"><dt className="inline text-tf-muted">{text.source}: </dt><dd className="inline break-all">{dimension.provenance.source_urls.join(', ')}</dd></div>
                  <div className="min-w-0"><dt className="inline text-tf-muted">{text.revision}: </dt><dd className="inline break-all font-mono">{dimension.provenance.source_revision}</dd></div>
                  <div className="min-w-0"><dt className="inline text-tf-muted">{text.coordinates}: </dt><dd className="inline break-words">{dimension.provenance.source_coordinates}</dd></div>
                </dl>
              ) : <p className="mt-2 text-tf-muted">{text.noProvenance}</p>}
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}
