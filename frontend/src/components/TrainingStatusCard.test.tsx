// @vitest-environment jsdom
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TrainingStatusCard from './TrainingStatusCard'

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function trainingStatusData() {
  return {
    training_data: {
      total_records: 120,
      has_direction: 80,
      direction_ratio: 0.6667,
      per_coin: {
        BTC: { total: 70, has_direction: 50 },
        ETH: { total: 50, has_direction: 30 },
      },
    },
    backfill: null,
    upgrade_threshold: {
      target: 100,
      current: 80,
      met: false,
      pct: 80,
    },
  }
}

describe('TrainingStatusCard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders real training data from a 200 response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data: trainingStatusData() })),
    )

    render(<TrainingStatusCard />)

    expect(await screen.findByText('方向標註進度')).toBeInTheDocument()
    expect(screen.getByText('80 / 100')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('66.7%')).toBeInTheDocument()
    expect(screen.getByLabelText('Status: 進行中')).toBeInTheDocument()
  })

  it('treats 404 as neutral not-enabled state without HTTP error copy', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(404, { ok: false })))

    render(<TrainingStatusCard />)

    const message = await screen.findByText('訓練資料未啟用')
    expect(message).toHaveAttribute('data-training-status-diagnostic', 'HTTP 404')
    expect(screen.getByLabelText('Status: 中性')).toBeInTheDocument()
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument()
    expect(screen.queryByText(/⚠/)).not.toBeInTheDocument()
  })

  it('treats timeout or network failure as neutral unavailable state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('signal timed out', 'TimeoutError')),
    )

    render(<TrainingStatusCard />)

    const message = await screen.findByText('訓練狀態暫不可用')
    expect(message).toHaveAttribute('data-training-status-diagnostic', 'signal timed out')
    expect(screen.getByLabelText('Status: 中性')).toBeInTheDocument()
    expect(screen.queryByText(/TimeoutError|HTTP|⚠/)).not.toBeInTheDocument()
  })

  it('keeps refresh 404 fail-soft without rendering transient HTTP copy', async () => {
    const intervalHandlers: TimerHandler[] = []
    vi.spyOn(globalThis, 'setInterval').mockImplementation((handler: TimerHandler) => {
      intervalHandlers.push(handler)
      return 1 as unknown as ReturnType<typeof setInterval>
    })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { ok: true, data: trainingStatusData() }))
      .mockResolvedValue(jsonResponse(404, { ok: false }))
    vi.stubGlobal('fetch', fetchMock)

    render(<TrainingStatusCard />)
    expect(await screen.findByText('80 / 100')).toBeInTheDocument()

    await act(async () => {
      const handler = intervalHandlers[0]
      if (typeof handler === 'function') {
        handler()
      }
    })

    await waitFor(() => expect(screen.getByText('訓練資料未啟用')).toBeInTheDocument())
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument()
    expect(screen.queryByText(/⚠/)).not.toBeInTheDocument()
  })
})
