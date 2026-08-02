// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

describe('Header 版本徽章', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('前端 bundle 版本與後端 runtime 版本分開顯示，且 health 不覆蓋前端版本', async () => {
    vi.stubEnv('VITE_FRONTEND_VERSION', 'v0.27.51')
    vi.stubEnv('VITE_BUNDLE_GIT_SHA', 'abc1234def5678')
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        data: { status: 'ok', version: 'v0.9.99', uptime_seconds: 12 },
      }),
    }) as unknown as typeof fetch
    vi.resetModules()
    const { default: Header } = await import('./Header')
    const { HermesI18nProvider } = await import('../hermes/hermesI18n')

    render(
      <MemoryRouter>
        <HermesI18nProvider><Header /></HermesI18nProvider>
      </MemoryRouter>
    )

    expect(await screen.findByText('Frontend v0.27.51 · abc1234 | Backend v0.9.99')).toBeTruthy()
  })

  it('frontend version 或 bundle SHA 缺失時顯示 degraded unversioned 狀態', async () => {
    vi.stubEnv('VITE_FRONTEND_VERSION', '')
    vi.stubEnv('VITE_BUNDLE_GIT_SHA', '')
    global.fetch = vi.fn().mockRejectedValue(new Error('health unavailable')) as unknown as typeof fetch
    vi.resetModules()
    const { default: Header } = await import('./Header')
    const { HermesI18nProvider } = await import('../hermes/hermesI18n')

    render(
      <MemoryRouter>
        <HermesI18nProvider><Header /></HermesI18nProvider>
      </MemoryRouter>
    )

    const badge = screen.getByTitle('這份前端 bundle 沒有版本資訊（未經發版流程建置），顯示的不是版號。')
    expect(badge.textContent).toBe('Frontend unversioned · unversioned-sha | Backend unversioned')
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
