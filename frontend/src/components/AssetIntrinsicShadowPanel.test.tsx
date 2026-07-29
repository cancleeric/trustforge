// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import AssetIntrinsicShadowPanel, { parseIntrinsicAssessment } from './AssetIntrinsicShadowPanel'

const names = [
  'issuance_predictability',
  'control_dispersion',
  'supply_verifiability',
  'governance_capture_resistance',
  'holder_concentration',
]

function provenance(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    source_urls: ['https://github.com/bitcoin/bitcoin'],
    source_revision: 'd0f6d995',
    content_hash: 'a'.repeat(64),
    evidence_kind: 'upstream_excerpt',
    source_coordinates: 'src/validation.cpp:L1-L2',
    as_of: '2026-07-28T00:00:00Z',
    fetched_at: '2026-07-28T00:00:00Z',
    ...overrides,
  }
}

// Real backend payload shape (10 top-level keys incl. conflict_detected).
function assessment() {
  return {
    schema_version: '1.0.0',
    mode: 'shadow',
    affects_official_score: false,
    asset_id: 'asset:btc',
    as_of: '2026-07-28T00:00:00Z',
    total_delta: 0,
    total_delta_cap: 0.08,
    conflict_detected: false,
    gate: { passed: false, known_count: 2, required_known: 3, source_family_count: 1, required_source_families: 2, reason_code: 'insufficient_coverage' },
    dimensions: names.map((name, index) => ({
      name,
      status: index < 2 ? 'known' : 'unknown',
      raw: index < 2 ? 1 : null,
      normalized: index < 2 ? 1 : null,
      weight: 0.032,
      signed_delta: 0,
      reason_code: index < 2 ? 'coverage_gate_not_met' : 'fact_unavailable',
      coverage: index < 2 ? 'Bitcoin Core lines 1-2' : 'no PIT-visible verified fact',
      provenance: index < 2 ? provenance() : null,
    })),
  }
}

function renderPanel(value: unknown) {
  return render(<HermesI18nProvider><AssetIntrinsicShadowPanel value={value} /></HermesI18nProvider>)
}

function passedAssessment() {
  const payload = assessment()
  payload.gate = { passed: true, known_count: 3, required_known: 3, source_family_count: 2, required_source_families: 2, reason_code: 'eligible' }
  payload.dimensions[2] = {
    ...payload.dimensions[0],
    name: names[2],
    reason_code: 'eligible',
    signed_delta: 0.016,
    provenance: {
      ...payload.dimensions[0].provenance!,
      source_urls: ['https://example.org/upstream'],
    },
  }
  payload.dimensions[0].reason_code = 'eligible'
  payload.dimensions[0].signed_delta = 0.016
  payload.dimensions[1].reason_code = 'eligible'
  payload.dimensions[1].signed_delta = 0.016
  payload.total_delta = 0.048
  return payload
}

// A stale dimension (aged known fact); backend always attaches provenance.
function staleDim(name: string) {
  return {
    name, status: 'stale', raw: null, normalized: null, weight: 0.032, signed_delta: 0,
    reason_code: 'stale', coverage: 'fact aged past freshness window',
    provenance: provenance({ source_coordinates: 'docs/stale.md' }),
  }
}

// A conflicted dimension (divergent sources); backend always attaches provenance.
function conflictedDim(name: string) {
  return {
    name, status: 'conflicted', raw: null, normalized: null, weight: 0.032, signed_delta: 0,
    reason_code: 'fact_conflicted', coverage: 'divergent observations across sources',
    provenance: {
      source_urls: ['https://alpha.example/obs', 'https://beta.example/obs'],
      source_revision: 'conflicted-1',
      content_hash: 'b'.repeat(64),
      evidence_kind: 'decision_record',
      source_coordinates: 'ADR-7',
      as_of: '2026-07-28T00:00:00Z',
      fetched_at: '2026-07-28T00:00:00Z',
    },
  }
}

function officialAssessment() {
  const payload = passedAssessment()
  return {
    ...payload,
    mode: 'official',
    affects_official_score: true,
    release_capability: { capability: 'asset_intrinsic', promoted_at: '2026-07-28T00:00:00Z' },
    promotion_receipt: {
      receipt_id: 'rc-001',
      policy_digest: 'sha256:policy-abc',
      decision: 'pass',
      reasons: ['non-inferiority gate met', 'calibration not worsened'],
      calibration_claim: { brier_before: 0.2, brier_after: 0.18 },
    },
  }
}

describe('AssetIntrinsicShadowPanel', () => {
  it('renders honest gate, five dimensions, zero delta, and provenance', () => {
    const { container } = renderPanel(assessment())
    expect(screen.getByText('SHADOW／不影響正式信任分')).toBeInTheDocument()
    expect(screen.getByText('覆蓋不足，所有調整維持 0')).toBeInTheDocument()
    expect(screen.getByText('2/3')).toBeInTheDocument()
    expect(screen.getByText('1/2')).toBeInTheDocument()
    expect(container.textContent?.match(/0\.000/g)).toHaveLength(6)
    expect(screen.getByText('持幣集中度')).toBeInTheDocument()
    expect(screen.getAllByText('證據溯源')).toHaveLength(5)
  })

  it('renders the three-layer framing eyebrow and intro via hermesI18n', () => {
    renderPanel(assessment())
    expect(screen.getByText('資產結構 · ASSET STRUCTURE')).toBeInTheDocument()
    expect(screen.getByText(/這一層只看資產本身的結構事實/)).toBeInTheDocument()
    // per-dimension "what this measures" copy renders for all five dimensions
    // (each description is unique to one <p>, avoiding nested-text matches).
    expect(screen.getByText(/發行規則與節奏/)).toBeInTheDocument()
    expect(screen.getByText(/變更這個資產協議的權限/)).toBeInTheDocument()
    expect(screen.getByText(/流通數量能否在鏈上/)).toBeInTheDocument()
    expect(screen.getByText(/分配表決與提案權/)).toBeInTheDocument()
    expect(screen.getByText(/持有量在各大位址/)).toBeInTheDocument()
  })

  it('renders Verified and gate passed only for a canonical eligible payload', () => {
    renderPanel(passedAssessment())
    expect(screen.getByText('覆蓋閘已通過')).toBeInTheDocument()
    expect(screen.getAllByText(/已驗證/)).toHaveLength(3)
    expect(screen.getByText('+0.048')).toBeInTheDocument()
  })

  it('accepts canonical unknown decision-record provenance without an external URL', () => {
    const payload = assessment()
    payload.dimensions[4].reason_code = 'fact_unknown'
    payload.dimensions[4].provenance = {
      source_urls: [],
      source_revision: 'decision-2026-07-28',
      content_hash: 'b'.repeat(64),
      evidence_kind: 'decision_record',
      source_coordinates: 'ADR-42',
      as_of: '2026-07-28T00:00:00Z',
      fetched_at: '2026-07-28T00:00:00Z',
    }
    expect(parseIntrinsicAssessment(payload)).not.toBeNull()
    const { container } = renderPanel(payload)
    expect(screen.getByText('ADR-42')).toBeInTheDocument()
    expect(container.textContent?.match(/未知/g)).toHaveLength(3)
  })

  it('does not render for legacy absent or null fields', () => {
    const { container, rerender } = renderPanel(undefined)
    expect(container).toBeEmptyDOMElement()
    rerender(<HermesI18nProvider><AssetIntrinsicShadowPanel value={null} /></HermesI18nProvider>)
    expect(container).toBeEmptyDOMElement()
  })

  it('fails closed for malformed, duplicate, and non-finite data without throwing', () => {
    expect(parseIntrinsicAssessment({ ...assessment(), total_delta: Number.NaN })).toBeNull()
    const duplicate = assessment()
    duplicate.dimensions[4].name = duplicate.dimensions[0].name
    expect(parseIntrinsicAssessment(duplicate)).toBeNull()
    const dishonestGate = assessment()
    dishonestGate.total_delta = 0.08
    expect(parseIntrinsicAssessment(dishonestGate)).toBeNull()
    const inconsistentCount = assessment()
    inconsistentCount.gate.known_count = 3
    inconsistentCount.gate.passed = true
    expect(parseIntrinsicAssessment(inconsistentCount)).toBeNull()
    expect(parseIntrinsicAssessment({ ...assessment(), schema_version: '2.0.0' })).toBeNull()
    expect(parseIntrinsicAssessment({ ...assessment(), as_of: '2026-07-28T00:00:00' })).toBeNull()
    const dishonestUnknown = assessment()
    dishonestUnknown.dimensions[4].raw = 0
    expect(parseIntrinsicAssessment(dishonestUnknown)).toBeNull()
    const outOfRangeKnown = assessment()
    outOfRangeKnown.dimensions[0].normalized = 1.1
    expect(parseIntrinsicAssessment(outOfRangeKnown)).toBeNull()
    const missingKnownProvenance = assessment()
    missingKnownProvenance.dimensions[0].provenance = null
    expect(parseIntrinsicAssessment(missingKnownProvenance)).toBeNull()
    const knownUpstreamWithoutUrl = assessment()
    knownUpstreamWithoutUrl.dimensions[0].provenance!.source_urls = []
    expect(parseIntrinsicAssessment(knownUpstreamWithoutUrl)).toBeNull()
    const unknownUpstreamWithoutUrl = assessment()
    unknownUpstreamWithoutUrl.dimensions[4].reason_code = 'fact_unknown'
    unknownUpstreamWithoutUrl.dimensions[4].provenance = {
      ...unknownUpstreamWithoutUrl.dimensions[0].provenance!,
      source_urls: [],
    }
    expect(parseIntrinsicAssessment(unknownUpstreamWithoutUrl)).toBeNull()
    const badUrl = assessment()
    badUrl.dimensions[0].provenance!.source_urls = ['https://user@example.com/source']
    expect(parseIntrinsicAssessment(badUrl)).toBeNull()
    const explicitPort = assessment()
    explicitPort.dimensions[0].provenance!.source_urls = ['https://example.com:443/source']
    expect(parseIntrinsicAssessment(explicitPort)).toBeNull()
    const extraField = { ...assessment(), unexpected: true }
    expect(parseIntrinsicAssessment(extraField)).toBeNull()
    const badHash = assessment()
    badHash.dimensions[0].provenance!.content_hash = 'not-a-sha256'
    expect(parseIntrinsicAssessment(badHash)).toBeNull()
    const futureProvenance = assessment()
    futureProvenance.dimensions[0].provenance!.fetched_at = '2026-07-29T00:00:00Z'
    expect(parseIntrinsicAssessment(futureProvenance)).toBeNull()
    const reversedProvenanceTime = assessment()
    reversedProvenanceTime.dimensions[0].provenance!.as_of = '2026-07-28T00:00:00Z'
    reversedProvenanceTime.dimensions[0].provenance!.fetched_at = '2026-07-27T00:00:00Z'
    expect(parseIntrinsicAssessment(reversedProvenanceTime)).toBeNull()
    const wrongConstants = assessment()
    wrongConstants.total_delta_cap = 1
    expect(parseIntrinsicAssessment(wrongConstants)).toBeNull()
    const wrongReason = assessment()
    wrongReason.dimensions[0].reason_code = 'eligible'
    expect(parseIntrinsicAssessment(wrongReason)).toBeNull()
    renderPanel({ mode: 'shadow', affects_official_score: true })
    expect(screen.getByText(/資產結構資料格式不相容/)).toBeInTheDocument()
    expect(screen.queryByText('已驗證')).not.toBeInTheDocument()
    expect(screen.queryByText('覆蓋閘已通過')).not.toBeInTheDocument()
  })

  it('renders English copy and long provenance without unsafe links', () => {
    document.cookie = 'trustforge_hermes_locale=en'
    const payload = assessment()
    payload.dimensions[0].provenance!.source_coordinates = 'x'.repeat(500)
    renderPanel(payload)
    expect(screen.getByText('SHADOW / does not affect official trust score')).toBeInTheDocument()
    expect(screen.getByText(/Insufficient coverage/)).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    document.cookie = 'trustforge_hermes_locale=zh-TW'
  })

  // -- P0 drift fixes (contract drift A + B) ---------------------------------

  it('P0 drift A: accepts the real 10-key payload incl. conflict_detected; rejects the legacy 9-key drift', () => {
    const real = assessment()
    expect(parseIntrinsicAssessment(real)).not.toBeNull()
    // The legacy frontend shape (no conflict_detected) must fail closed — this
    // is the regression guard against re-introducing the parser/fixture drift.
    const legacy = { ...real }
    delete (legacy as Record<string, unknown>).conflict_detected
    expect(parseIntrinsicAssessment(legacy)).toBeNull()
    // conflict_detected must be a boolean, not a truthy coerce.
    expect(parseIntrinsicAssessment({ ...real, conflict_detected: 'false' })).toBeNull()
  })

  it('P0 drift B: accepts stale and conflicted dimensions instead of dropping the whole panel', () => {
    const stale = assessment()
    stale.dimensions[2] = staleDim(names[2])
    expect(parseIntrinsicAssessment(stale)).not.toBeNull()
    renderPanel(stale)
    expect(screen.getByText(/過時 ·/)).toBeInTheDocument()

    const conflicted = assessment()
    conflicted.dimensions[4] = conflictedDim(names[4])
    conflicted.conflict_detected = true
    expect(parseIntrinsicAssessment(conflicted)).not.toBeNull()
    renderPanel(conflicted)
    expect(screen.getByText(/衝突 ·/)).toBeInTheDocument()
    expect(screen.getByText(/記錄到至少一個維度的來源衝突/)).toBeInTheDocument()
  })

  it('enforces the conflict_detected cross-check invariant (fail closed on mismatch)', () => {
    // claims a conflict but carries no conflicted dimension
    const falseAlarm = assessment()
    falseAlarm.conflict_detected = true
    expect(parseIntrinsicAssessment(falseAlarm)).toBeNull()
    // hides a real conflicted dimension by claiming conflict_detected=false
    const coverUp = assessment()
    coverUp.dimensions[3] = conflictedDim(names[3])
    // conflict_detected stays false here
    expect(parseIntrinsicAssessment(coverUp)).toBeNull()
    // stale/conflicted must carry non-null provenance
    const staleNoProv = assessment()
    staleNoProv.dimensions[2] = { ...staleDim(names[2]), provenance: null }
    expect(parseIntrinsicAssessment(staleNoProv)).toBeNull()
    const conflictedNoProv = assessment()
    conflictedNoProv.dimensions[2] = { ...conflictedDim(names[2]), provenance: null }
    conflictedNoProv.conflict_detected = true
    expect(parseIntrinsicAssessment(conflictedNoProv)).toBeNull()
    // wrong reason codes for the new statuses
    const staleWrongReason = assessment()
    staleWrongReason.dimensions[2] = { ...staleDim(names[2]), reason_code: 'fact_unknown' }
    expect(parseIntrinsicAssessment(staleWrongReason)).toBeNull()
    const conflictedWrongReason = assessment()
    conflictedWrongReason.dimensions[2] = { ...conflictedDim(names[2]), reason_code: 'fact_unknown' }
    conflictedWrongReason.conflict_detected = true
    expect(parseIntrinsicAssessment(conflictedWrongReason)).toBeNull()
  })

  // -- #878 official parsing skeleton ---------------------------------------

  it('renders official only from a fully-receipted, release-capable and eligible payload', () => {
    const parsed = parseIntrinsicAssessment(officialAssessment())
    expect(parsed).not.toBeNull()
    expect(parsed?.mode).toBe('official')
    const { container } = renderPanel(officialAssessment())
    expect(container.querySelector('[data-intrinsic-mode="official"]')).not.toBeNull()
    expect(screen.getByText('OFFICIAL／已納入正式信任分')).toBeInTheDocument()
    expect(screen.getByText(/已記錄發行能力與 promotion receipt/)).toBeInTheDocument()
    expect(screen.getByText(/Promotion receipt: rc-001/)).toBeInTheDocument()
  })

  it('official skeleton fail-closed: missing release_capability or receipt -> downgrade', () => {
    const noCapability = officialAssessment()
    delete (noCapability as Record<string, unknown>).release_capability
    expect(parseIntrinsicAssessment(noCapability)).toBeNull()
    const noReceipt = officialAssessment()
    delete (noReceipt as Record<string, unknown>).promotion_receipt
    expect(parseIntrinsicAssessment(noReceipt)).toBeNull()
    // receipt missing a field
    const partialReceipt = officialAssessment()
    delete (partialReceipt.promotion_receipt as Record<string, unknown>).calibration_claim
    expect(parseIntrinsicAssessment(partialReceipt)).toBeNull()
    // decision !== pass (e.g. block) masquerading as official
    const blocked = officialAssessment()
    blocked.promotion_receipt.decision = 'block'
    expect(parseIntrinsicAssessment(blocked)).toBeNull()
    // official must claim affects_official_score=true; a shadow-style false is inconsistent
    const shadowish = officialAssessment()
    shadowish.affects_official_score = false
    expect(parseIntrinsicAssessment(shadowish)).toBeNull()
    // a bare shadow payload cannot self-declare official by just flipping mode
    const fakeOfficial = assessment()
    fakeOfficial.mode = 'official'
    expect(parseIntrinsicAssessment(fakeOfficial)).toBeNull()
    const emptyCapability = officialAssessment()
    emptyCapability.release_capability = {} as typeof emptyCapability.release_capability
    expect(parseIntrinsicAssessment(emptyCapability)).toBeNull()
    const wrongCapability = officialAssessment()
    wrongCapability.release_capability.capability = 'other' as 'asset_intrinsic'
    expect(parseIntrinsicAssessment(wrongCapability)).toBeNull()
    const unpromoted = officialAssessment()
    unpromoted.gate = assessment().gate
    unpromoted.total_delta = 0
    unpromoted.dimensions = assessment().dimensions
    expect(parseIntrinsicAssessment(unpromoted)).toBeNull()
    // all invalid official payloads downgrade to the unavailable notice
    renderPanel(blocked)
    expect(screen.getByText(/資產結構資料格式不相容/)).toBeInTheDocument()
  })

  // -- AC5 banned-words audit ------------------------------------------------

  it('AC5: panel copy contains no prescriptive or directional banned words (zh + en)', () => {
    // Canonical banned set from the issue (BTC 應更高 / 優於 / 建議 / 預期將) plus a
    // few clearly prescriptive/directional verbs. Measurement nouns such as
    // 「可預測性」/ "predictability" are intentional and excluded from the audit.
    const bannedZh = [/應更高/, /優於/, /建議/, /預期將/, /推薦/, /應該/]
    document.cookie = 'trustforge_hermes_locale=zh-TW'
    const zh = renderPanel(passedAssessment())
    const zhText = zh.container.textContent ?? ''
    for (const pattern of bannedZh) expect(zhText).not.toMatch(pattern)

    document.cookie = 'trustforge_hermes_locale=en'
    const en = renderPanel(passedAssessment())
    const enText = en.container.textContent ?? ''
    const bannedEn = [/should be higher/i, /better than/i, /\brecommend/i, /expected to/i, /outperform/i, /will (rise|fall)/i]
    for (const pattern of bannedEn) expect(enText).not.toMatch(pattern)
    document.cookie = 'trustforge_hermes_locale=zh-TW'
  })
})
