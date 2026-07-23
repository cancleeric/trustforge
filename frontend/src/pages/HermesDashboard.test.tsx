// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { registerAnalysisQuestion } from '../lib/endpoints'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import HermesDashboard from './HermesDashboard'

vi.mock('../lib/endpoints', () => ({
  getOverview: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getCosts: vi.fn().mockResolvedValue({ ok: true, data: { total_cost_usd: 0 } }),
  getHealth: vi.fn().mockResolvedValue({ ok: true, data: { version: 'dev' } }),
  getAnalysisSnapshot: vi.fn().mockResolvedValue({ ok: false, error: { code: 'snapshot_pending', message: 'pending' } }),
  getAnalysisFlow: vi.fn().mockResolvedValue({ ok: true, data: { agent: 'hermes', state: 'continuous', stages: [], updated_at: 'now' } }),
  getAnalysisJourney: vi.fn().mockResolvedValue({ ok: true, data: { jobs: [], dead_letters: [], updated_at: 'now' } }),
  getAnalysisQuestionContext: vi.fn().mockResolvedValue({ ok: true, data: { query: '', matches: [], conversation: [], retrieval: 'test' } }),
  getHermesUpgrades: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'no_request', message: 'no request' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { accepted: true } }),
}))

function DashboardHistoryControls() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/?qa=1&coin=SOL')}>plain entry</button>
}

function LocationProbe() {
  return <output aria-label="location">{useLocation().search}</output>
}

describe('HermesDashboard workspace navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      media: '(max-width: 560px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  it('keeps Analyze workspace open after top-bar click', async () => {
    vi.useRealTimers()
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: '分析' }))

    expect(screen.getByRole('region', { name: 'analyze workspace' })).toBeInTheDocument()

    await new Promise((resolve) => window.setTimeout(resolve, 450))

    expect(screen.getByRole('region', { name: 'analyze workspace' })).toBeInTheDocument()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  }, 15_000)

  it('renders only the dashboard composer when Analyze is embedded on desktop', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1&workspace=analyze']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.queryByLabelText('問題')).not.toBeInTheDocument()
  })

  it('exposes the divergence dock as a named, focusable button and opens it on click', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    const divergenceDock = screen.getByRole('button', { name: '跨來源分歧' })
    expect(divergenceDock).toHaveProperty('tabIndex', 0)
    divergenceDock.focus()
    expect(divergenceDock).toHaveFocus()
    fireEvent.click(divergenceDock)

    expect(screen.getByRole('dialog')).toHaveTextContent('跨來源分歧')
  })

  it.each(['Enter', ' '])('opens the divergence drilldown once with the %s key', (key) => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    const divergenceDock = screen.getByRole('button', { name: '跨來源分歧' })
    fireEvent.keyDown(divergenceDock, { key })

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog')).toHaveTextContent('跨來源分歧')
  })

  it.each(['基本面', '價格催化因子'])('maps %s to hypothesis', async (mode) => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider><LocationProbe />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('風險評估'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: mode } })
    fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))

    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent('type=hypothesis'))
  })

  it('resets missing mode and question on a history entry', async () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1&coin=ETH&mode=catalyst&q=舊問題']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
        <DashboardHistoryControls />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('價格催化因子'))
    fireEvent.click(screen.getByRole('button', { name: 'plain entry' }))

    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('風險評估'))
    expect(screen.getByRole('textbox')).toHaveValue('分析SOL近期市場狀況，整合多源資料')
  })
})
