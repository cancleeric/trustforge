// @vitest-environment jsdom

import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getAnalyze } from '../lib/endpoints'
import AnalyzePage from './AnalyzePage'

vi.mock('../lib/endpoints', () => ({
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'timeout' } }),
}))

function renderAnalyze(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <AnalyzePage />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

describe('AnalyzePage manual execution', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('starts a manual run directly instead of waiting for the background queue', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

    await waitFor(() => expect(getAnalyze).toHaveBeenCalledWith(
      {
        coin: 'BTC',
        type: 'multi_source',
        q: '分析BTC近期市場狀況',
        sample: undefined,
      },
      expect.any(AbortSignal),
    ))
  })

  it('keeps sample mode as an immediate local/demo analysis path', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&q=demo&sample=1')

    await waitFor(() => expect(getAnalyze).toHaveBeenCalledWith(
      { coin: 'BTC', type: 'multi_source', q: 'demo', sample: '1' },
      expect.any(AbortSignal),
    ))
  })
})
