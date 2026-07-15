// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import HermesTopBar from './HermesTopBar'
import { HermesI18nProvider } from './hermesI18n'

describe('HermesTopBar', () => {
  it('exposes every operational workspace from the flagship dashboard', () => {
    render(
      <MemoryRouter>
        <HermesI18nProvider><HermesTopBar version="v0.test · GALAXY" costLedger={1.25} /></HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText('v0.test · GALAXY')).toBeInTheDocument()
    expect(screen.getByText('$1.2500')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '分析' })).toHaveAttribute('href', '/analyze')
    expect(screen.getByRole('link', { name: '比較' })).toHaveAttribute('href', '/compare')
    expect(screen.getByRole('link', { name: '歷史趨勢' })).toHaveAttribute('href', '/history')
    expect(screen.getByRole('link', { name: '來源狀態' })).toHaveAttribute('href', '/status')
    expect(screen.getByRole('link', { name: '成本' })).toHaveAttribute('href', '/costs')

    fireEvent.click(screen.getByRole('button', { name: '切換語言' }))
    expect(screen.getByRole('link', { name: 'ANALYZE' })).toHaveAttribute('href', '/analyze')
    expect(document.cookie).toContain('trustforge_hermes_locale=en')
  })
})
