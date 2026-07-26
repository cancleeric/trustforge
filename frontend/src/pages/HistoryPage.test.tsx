// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import type { HistoryData } from '../lib/types'
import { getHistory } from '../lib/endpoints'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import HistoryPage from './HistoryPage'

vi.mock('../components/TrustHistoryChart', () => ({
  default: ({ history }: { history: Array<{ coin: string }> }) => (
    <div data-testid="history-chart">{history[0]?.coin}</div>
  ),
}))

vi.mock('../lib/endpoints', () => ({
  getHistory: vi.fn(),
}))

function historyData(coin: string): HistoryData {
  return {
    coin,
    days: 30,
    history: [{
      coin,
      date: '2026-07-15',
      trust_score: 0.59,
      direction: '中性',
      calibrated_confidence: 0.71,
      decision_state: 'normal',
      generated_at: '2026-07-15T00:00:00Z',
    }],
  }
}

function renderHistoryPage(initialUrl = '/?coin=BTC&days=30') {
  return render(
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[initialUrl]}>
        <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
          <HistoryPage />
        </BridgeHologramProvider>
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

describe('HistoryPage', () => {
  it('keeps the last complete snapshot visible when refresh is rate limited', async () => {
    vi.mocked(getHistory)
      .mockResolvedValueOnce({ ok: true, data: historyData('BTC') })
      .mockResolvedValueOnce({ ok: false, error: { code: 'rate_limited', message: '請求過於頻繁，請 30 秒後再試' } })

    renderHistoryPage()

    expect(await screen.findByTestId('history-chart')).toHaveTextContent('BTC')

    fireEvent.click(screen.getByRole('radio', { name: 'ETH' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('rate_limited')
    })
    expect(screen.getByRole('alert')).toHaveTextContent('目前保留上一個完整快照')
    expect(screen.getByTestId('history-chart')).toHaveTextContent('BTC')
  })
})
