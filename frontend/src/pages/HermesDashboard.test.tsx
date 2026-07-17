// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
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
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { accepted: true } }),
}))

describe('HermesDashboard workspace navigation', () => {
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
})
