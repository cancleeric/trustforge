// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SnapshotModal from './SnapshotModal'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import type { Evidence } from '../lib/types'

const evidence: Evidence = {
  source: 'ohlcv-csv',
  fetched_at: '2026-05-31T00:00:00Z',
  content_reference: 'BTC/USDT close 77001.9->73674.4',
  related_claim: 'BTC 市場判斷',
  source_url: '',
  kind: 'price',
  trust: 0.625,
  trust_components: {
    reputation: 0.95,
    corroboration: 0,
    recency: 1,
    manipulation: 0,
  },
  flags: [],
  info_flags: [],
  data_lineage: {
    dataset_role: 'competition_baseline',
    dataset_name: 'Crypto Market Dataset',
    dataset_generated_at: '2026-06-09T09:16:29Z',
    file: 'BTC_daily_ohlcv.csv',
    sha256: 'ae783a4e3f155e389fcec904abb8c04bf8b657d3abaa81e6770f2d4aba6245e4',
    rows: 1826,
    coverage: { start_date: '2021-06-01', end_date: '2026-05-31' },
    analysis_window: '2026-05-18~2026-05-31',
    trading_pair: 'BTCUSDT',
    time_basis: 'UTC',
    interval: '1d',
    price_unit: 'USDT',
    columns: ['date', 'open', 'high', 'low', 'close', 'volume'],
  },
}

describe('SnapshotModal data lineage', () => {
  function downloadedPayload() {
    const download = screen.getByRole('link', { name: '下載 JSON ⤓' })
    const href = download.getAttribute('href') ?? ''
    const prefix = 'data:application/json,'
    expect(href.startsWith(prefix)).toBe(true)
    return JSON.parse(decodeURIComponent(href.slice(prefix.length)))
  }

  it('renders lineage details and exports the same lineage in downloaded JSON', () => {
    render(<HermesI18nProvider><SnapshotModal ev={evidence} onClose={vi.fn()} /></HermesI18nProvider>)

    expect(screen.getByText('BTC_daily_ohlcv.csv')).toBeInTheDocument()
    expect(screen.getByText('2021-06-01 ~ 2026-05-31')).toBeInTheDocument()
    expect(screen.getByText('BTCUSDT')).toBeInTheDocument()
    expect(screen.getByText('date, open, high, low, close, volume')).toBeInTheDocument()
    expect(screen.getByText(evidence.data_lineage?.sha256 ?? '')).toBeInTheDocument()

    const payload = downloadedPayload()

    expect(payload.data_lineage).toEqual(evidence.data_lineage)
    expect(payload.source).toBe(evidence.source)
  })

  it('exports null lineage for non-file evidence instead of omitting the field', () => {
    render(<HermesI18nProvider><SnapshotModal ev={{ ...evidence, data_lineage: null, kind: "news" }} onClose={vi.fn()} /></HermesI18nProvider>)

    const payload = downloadedPayload()

    expect(payload).toHaveProperty('data_lineage', null)
    expect(screen.getByText(/無檔案型可重現血緣鏈/)).toBeInTheDocument()
  })

  it('surfaces dataset_generated_at and price_unit in lineage UI and export JSON', () => {
    render(<HermesI18nProvider><SnapshotModal ev={evidence} onClose={vi.fn()} /></HermesI18nProvider>)

    expect(screen.getByText(evidence.data_lineage?.dataset_generated_at ?? '')).toBeInTheDocument()
    expect(screen.getByText(evidence.data_lineage?.price_unit ?? '')).toBeInTheDocument()

    const payload = downloadedPayload()

    expect(payload.data_lineage.dataset_generated_at).toBe(evidence.data_lineage?.dataset_generated_at)
    expect(payload.data_lineage.price_unit).toBe(evidence.data_lineage?.price_unit)
  })
})
