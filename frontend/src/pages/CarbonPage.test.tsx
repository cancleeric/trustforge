// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { getCarbon } from '../lib/endpoints'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import CarbonPage from './CarbonPage'

vi.mock('../lib/endpoints', () => ({
  getCarbon: vi.fn(),
}))

describe('CarbonPage', () => {
  it('renders a stable error code when carbon data cannot be loaded', async () => {
    vi.mocked(getCarbon).mockResolvedValueOnce({
      ok: false,
      error: { code: 'upstream_error', message: 'unavailable' },
    })

    render(
      <HermesI18nProvider>
        <CarbonPage />
      </HermesI18nProvider>,
    )

    expect(await screen.findByText('carbon_load_failed')).toBeInTheDocument()
  })
})
