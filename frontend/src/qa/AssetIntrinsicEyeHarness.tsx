import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import AssetIntrinsicShadowPanel from '../components/AssetIntrinsicShadowPanel'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import '../index.css'

const names = [
  'issuance_predictability',
  'control_dispersion',
  'supply_verifiability',
  'governance_capture_resistance',
  'holder_concentration',
]

function payload(long = false) {
  const coordinates = long ? `src/consensus/${'very-long-provenance-segment-'.repeat(80)}:L1-L200` : 'src/consensus/rules.cpp:L1-L20'
  return {
    schema_version: '1.0.0',
    mode: 'shadow',
    affects_official_score: false,
    asset_id: 'asset:visual-fixture',
    as_of: '2026-07-28T00:00:00Z',
    total_delta: 0.048,
    total_delta_cap: 0.08,
    conflict_detected: false,
    gate: {
      passed: true,
      known_count: 3,
      required_known: 3,
      source_family_count: 2,
      required_source_families: 2,
      reason_code: 'eligible',
    },
    dimensions: names.map((name, index) => {
      const known = index < 3
      return {
        name,
        status: known ? 'known' : 'unknown',
        raw: known ? 1 : null,
        normalized: known ? 1 : null,
        weight: 0.032,
        signed_delta: known ? 0.016 : 0,
        reason_code: known ? 'eligible' : 'fact_unavailable',
        coverage: long
          ? `Coverage limitation ${'with-long-unbroken-content-'.repeat(80)}`
          : known ? 'PIT-visible public evidence' : 'No PIT-visible verified fact',
        provenance: known ? {
          source_urls: [index === 2 ? 'https://example.org/upstream' : 'https://github.com/bitcoin/bitcoin'],
          source_revision: long ? 'r'.repeat(256) : 'd0f6d995',
          content_hash: 'a'.repeat(64),
          evidence_kind: 'upstream_excerpt',
          source_coordinates: coordinates,
          as_of: '2026-07-28T00:00:00Z',
          fetched_at: '2026-07-28T00:00:00Z',
        } : null,
      }
    }),
  }
}

function fixture(scenario: string) {
  if (scenario === 'malformed') return { mode: 'official', affects_official_score: true }
  const value = payload(scenario === 'long')
  if (scenario === 'official-forgery') {
    return {
      ...value,
      mode: 'official',
      affects_official_score: true,
      official_state: {
        schema_version: 'trustforge.intrinsic-official-state/v1',
        state: 'official',
        capability_id: 'asset-intrinsic-v1',
        verified_at: '2026-07-28T00:00:00Z',
        expires_at: null,
        release_id: 'release-eye',
        reason: 'verified',
      },
    }
  }
  return value
}

const params = new URLSearchParams(window.location.search)
const scenario = params.get('scenario') ?? 'shadow'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HermesI18nProvider>
      <main className="min-h-screen overflow-x-hidden bg-tf-bg p-3 text-tf-text sm:p-8" data-eye-scenario={scenario}>
        <div className="mx-auto max-w-5xl">
          <AssetIntrinsicShadowPanel value={fixture(scenario)} />
        </div>
      </main>
    </HermesI18nProvider>
  </StrictMode>,
)
