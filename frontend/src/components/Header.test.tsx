// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

describe('Header 版本徽章', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('VITE_GIT_SHA 有值時，版本徽章顯示 git sha 前 7 碼', async () => {
    vi.stubEnv('VITE_GIT_SHA', 'abc1234def')
    vi.stubEnv('VITE_RELEASE_VERSION', 'v9.9.9')
    vi.resetModules()
    const { default: Header } = await import('./Header')
    const { HermesI18nProvider } = await import('../hermes/hermesI18n')

    render(
      <MemoryRouter>
        <HermesI18nProvider><Header /></HermesI18nProvider>
      </MemoryRouter>
    )

    const badge = screen.getByTitle('部署版本（release / git sha）')
    expect(badge.textContent).toBe('v9.9.9 · abc1234')
  })

  it('VITE_GIT_SHA 未設定（空字串）時，版本徽章 fallback 顯示 dev', async () => {
    vi.stubEnv('VITE_GIT_SHA', '')
    vi.stubEnv('VITE_RELEASE_VERSION', 'v9.9.9')
    vi.resetModules()
    const { default: Header } = await import('./Header')
    const { HermesI18nProvider } = await import('../hermes/hermesI18n')

    render(
      <MemoryRouter>
        <HermesI18nProvider><Header /></HermesI18nProvider>
      </MemoryRouter>
    )

    const badge = screen.getByTitle('部署版本（release / git sha）')
    expect(badge.textContent).toBe('v9.9.9 · dev')
  })
})
