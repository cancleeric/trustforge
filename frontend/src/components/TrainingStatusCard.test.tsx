// @vitest-environment jsdom
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TrainingStatusCard from './TrainingStatusCard'

const okPayload = {
  ok: true,
  data: {
    training_data: {
      total_records: 12,
      has_direction: 9,
      direction_ratio: 0.75,
      per_coin: {
        BTC: { total: 5, has_direction: 4 },
        ETH: { total: 7, has_direction: 5 },
      },
    },
    backfill: null,
    upgrade_threshold: {
      target: 20,
      current: 9,
      met: false,
      pct: 45,
    },
  },
}

describe('TrainingStatusCard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    vi.stubGlobal('AbortSignal', {
      timeout: vi.fn(() => undefined),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders real training data from a successful response', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => okPayload,
    } as Response)

    render(<TrainingStatusCard />)

    expect(await screen.findByText('9 / 20')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('75.0%')).toBeInTheDocument()
    expect(screen.getByText('BTC')).toBeInTheDocument()
  })

  it('treats a missing optional training-status route as neutral unavailable', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    render(<TrainingStatusCard />)

    expect(await screen.findByText('訓練資料未啟用')).toBeInTheDocument()
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^⚠/)).not.toBeInTheDocument()
    expect(screen.getByText('訓練資料未啟用')).toHaveAttribute(
      'data-diagnostic',
      'training-status endpoint returned 404',
    )
  })

  it('keeps timeout and network failures neutral with diagnostic context', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('The operation timed out.'))

    render(<TrainingStatusCard />)

    const status = await screen.findByText('訓練狀態暫不可用')
    expect(status).toBeInTheDocument()
    expect(status).toHaveAttribute('data-diagnostic', 'The operation timed out.')
    expect(screen.queryByText(/^⚠/)).not.toBeInTheDocument()
  })

  it('keeps deterministic server failures visible as errors', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 500,
    } as Response)

    render(<TrainingStatusCard />)

    await waitFor(() => {
      expect(screen.getByText('⚠ HTTP 500')).toBeInTheDocument()
    })
  })
})
