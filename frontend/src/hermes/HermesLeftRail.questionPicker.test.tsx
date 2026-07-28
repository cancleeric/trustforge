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
