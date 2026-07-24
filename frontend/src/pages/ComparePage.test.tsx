// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import ComparePage from './ComparePage'

vi.mock('../lib/endpoints', () => ({
  getComparisonSnapshot: vi.fn(),
  registerAnalysisComparison: vi.fn(),
}))

function renderPage(initialUrl = '/compare') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <ComparePage />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

describe('ComparePage · 同層 Peer 比較入口（模組③ Wave 3）', () => {
  it('提供連到獨立 /peer-metrics 頁的連結，不把 Peer 比較掛在 COIN_POOL 雙幣表單上', () => {
    renderPage()
    const link = screen.getByRole('link', { name: /查看同層 Peer 比較/ })
    expect(link).toHaveAttribute('href', '/peer-metrics')
  })
})
