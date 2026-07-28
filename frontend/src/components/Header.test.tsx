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

/** N68：EcoLink 對比賽五幣結構上給不出資料（見 Header.tsx 該處註解），
 * 已從主導覽收起。這組測試守住兩件事：不准有人把它加回導覽、以及換上去的
 * /settings 入口不准再掉。用 render 後的實際 DOM 判定，不是字串比對檔案內容，
 * 所以改寫成 NavLink、換 i18n key、包一層元件都照樣抓得到。 */
describe('Header 導覽入口', () => {
  async function renderHeader() {
    vi.resetModules()
    const { default: Header } = await import('./Header')
    const { HermesI18nProvider } = await import('../hermes/hermesI18n')
    return render(
      <MemoryRouter>
        <HermesI18nProvider><Header /></HermesI18nProvider>
      </MemoryRouter>,
    )
  }

  it('不得把 /eco-link 掛回主導覽（點進去對五幣必定撲空）', async () => {
    const { container } = await renderHeader()
    const hrefs = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).not.toContain('/eco-link')
  })

  it('/settings 有導覽入口——它是全站唯一沒有連結指向的可用頁', async () => {
    const { container } = await renderHeader()
    const hrefs = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toContain('/settings')
  })
})
