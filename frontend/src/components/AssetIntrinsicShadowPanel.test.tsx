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

function assessment() {
  return {
    schema_version: '1.0.0',
    mode: 'shadow',
    affects_official_score: false,
    asset_id: 'asset:btc',
    as_of: '2026-07-28T00:00:00Z',
    total_delta: 0,
    total_delta_cap: 0.08,
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
      provenance: index < 2 ? {
        source_urls: ['https://github.com/bitcoin/bitcoin'],
        source_revision: 'd0f6d995',
        content_hash: 'a'.repeat(64),
        evidence_kind: 'upstream_excerpt',
        source_coordinates: 'src/validation.cpp:L1-L2',
        as_of: '2026-07-28T00:00:00Z',
        fetched_at: '2026-07-28T00:00:00Z',
      } : null,
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
    expect(screen.getByText(/Shadow 資料格式不相容/)).toBeInTheDocument()
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
})
