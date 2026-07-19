// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import HermesFirstRun from './HermesFirstRun'

describe('HermesFirstRun', () => {
  it('keeps the full-width mobile panel inside its padded viewport', () => {
    const css = fs.readFileSync(path.resolve('src/hermes/hermes.css'), 'utf8')
    expect(css).toMatch(/\.hermes-first-run>section\{[^}]*box-sizing:border-box;[^}]*width:min\(860px,100%\)/)
  })

  it('starts from one asset and one plain-language task', () => {
    const onStart = vi.fn()
    render(<HermesFirstRun onStart={onStart} onSkip={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'ETH' }))
    fireEvent.click(screen.getByRole('button', { name: /這則消息可信嗎/ }))
    fireEvent.click(screen.getByRole('button', { name: /開始第一次分析/ }))
    expect(onStart).toHaveBeenCalledWith('ETH', 'news', expect.stringContaining('驗證'))
  })
})
