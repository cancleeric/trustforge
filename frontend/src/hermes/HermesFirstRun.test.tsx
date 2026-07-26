// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import HermesFirstRun from './HermesFirstRun'

describe('HermesFirstRun', () => {
  it('starts from one asset and one plain-language task', () => {
    const onStart = vi.fn()
    render(
      <HermesI18nProvider>
        <HermesFirstRun onStart={onStart} onSkip={vi.fn()} />
      </HermesI18nProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'ETH' }))
    fireEvent.click(screen.getByRole('button', { name: /這則消息可信嗎/ }))
    fireEvent.click(screen.getByRole('button', { name: /開始第一次分析/ }))
    expect(onStart).toHaveBeenCalledWith('ETH', 'news', expect.stringContaining('驗證'))
  })
})
