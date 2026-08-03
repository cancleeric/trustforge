// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { HermesI18nProvider } from './hermesI18n'
import HermesLeftRail from './HermesLeftRail'

describe('#823 Hermes task question picker', () => {
  it('fills the existing textarea once and never submits', () => {
    const onQuery = vi.fn()
    const onPickCompetitionQuestion = vi.fn()
    const onSubmit = vi.fn()
    render(
      <MemoryRouter>
        <HermesI18nProvider>
          <HermesLeftRail
            hermesMessage=""
            hasOrder={false}
            focus="risk"
            coin="BTC"
            query=""
            submitLabel="送出"
            onQuery={onQuery}
            onPickCompetitionQuestion={onPickCompetitionQuestion}
            onSubmit={onSubmit}
            random={() => 0}
          />
        </HermesI18nProvider>
      </MemoryRouter>,
    )

    const picker = screen.getByRole('button', { name: '隨機競賽題目' })
    expect(picker).toHaveAttribute('aria-describedby', 'hermes-question-picker-hint')
    fireEvent.keyDown(picker, { key: 'Enter' })
    fireEvent.click(picker)

    expect(onPickCompetitionQuestion).toHaveBeenCalledTimes(1)
    expect(onPickCompetitionQuestion).toHaveBeenCalledWith(expect.stringMatching(/^請分析 BTC：/))
    expect(onQuery).not.toHaveBeenCalled()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByLabelText('交付 Hermes 的任務').tagName).toBe('TEXTAREA')
  })
})

describe('#1355 project goals modal', () => {
  function renderRail() {
    render(
      <MemoryRouter>
        <HermesI18nProvider>
          <HermesLeftRail
            hermesMessage=""
            hasOrder={false}
            focus="risk"
            coin="BTC"
            query=""
            submitLabel="送出"
            onQuery={vi.fn()}
            onSubmit={vi.fn()}
          />
        </HermesI18nProvider>
      </MemoryRouter>,
    )
  }

  it('opens over the dashboard and closes with the close button', () => {
    renderRail()

    fireEvent.click(screen.getByRole('button', { name: '🎯 專案目標' }))
    expect(screen.getByRole('dialog', { name: '專案目標' })).toBeInTheDocument()
    expect(screen.getByText('🎯 TrustForge 專案目標')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '關閉專案目標' }))
    expect(screen.queryByRole('dialog', { name: '專案目標' })).not.toBeInTheDocument()
  })

  it('closes when Escape is pressed', () => {
    renderRail()
    const trigger = screen.getByRole('button', { name: '🎯 專案目標' })
    fireEvent.click(trigger)

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: '專案目標' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('keeps keyboard focus inside the modal and restores it after close', () => {
    renderRail()
    const trigger = screen.getByRole('button', { name: '🎯 專案目標' })
    fireEvent.click(trigger)
    const close = screen.getByRole('button', { name: '關閉專案目標' })
    expect(close).toHaveFocus()

    fireEvent.keyDown(window, { key: 'Tab' })
    expect(close).toHaveFocus()
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    expect(close).toHaveFocus()

    fireEvent.click(close)
    expect(trigger).toHaveFocus()
  })
})
