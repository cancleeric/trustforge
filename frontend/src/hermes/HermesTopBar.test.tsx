// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import HermesTopBar from './HermesTopBar'
import { HermesI18nProvider } from './hermesI18n'

describe('HermesTopBar', () => {
  it('exposes every operational workspace from the flagship dashboard', () => {
    const onModuleSelect = vi.fn()
    const onHome = vi.fn()
    render(
      <MemoryRouter>
        <HermesI18nProvider><HermesTopBar version="v0.test · GALAXY" costLedger={1.25} onModuleSelect={onModuleSelect} onHome={onHome} /></HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText('v0.test · GALAXY')).toBeInTheDocument()
    expect(screen.getByText('$1.2500')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'HERMES 主頁' }))
    expect(onHome).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '分析' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '比較' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '歷史趨勢' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '來源狀態' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '成本' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '比較' }))
    expect(onModuleSelect).toHaveBeenCalledWith('compare')

    fireEvent.click(screen.getByRole('button', { name: '切換語言' }))
    expect(screen.getByRole('button', { name: 'ANALYZE' })).toBeInTheDocument()
    expect(document.cookie).toContain('trustforge_hermes_locale=en')
  })
})
