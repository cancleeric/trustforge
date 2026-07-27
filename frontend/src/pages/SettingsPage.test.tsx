// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import SettingsPage from './SettingsPage'

function renderPage() {
  return render(
    <HermesI18nProvider>
      <SettingsPage />
    </HermesI18nProvider>,
  )
}

describe('SettingsPage', () => {
  // N59：全路由真實點擊掃描（11 路由 × 2 語系 × 6 視窗）在 /settings 抓到三顆
  // `role="switch"` 的可及名稱是空字串。旁邊那兩支 input[type=range] 都有
  // aria-label，只有 Toggle 沒有——Row 的 label 只是同層一個 <p>，沒有 for／
  // aria-labelledby 任何關聯，所以螢幕閱讀器只會唸「switch, on」，唸不出這顆
  // 開關管的是什麼。這跟尺寸無關（22px 軌道早已用 ::after 撐到 26px 命中區，
  // 掃描報的 42x22 是儀器量 box 的誤報，不要再「修」一次）。
  it('N59: 每顆開關都有可及名稱', () => {
    renderPage()
    const switches = screen.getAllByRole('switch')
    expect(switches.length).toBeGreaterThan(0)
    for (const s of switches) {
      expect(s).toHaveAccessibleName()
    }
  })

  it('N59: 可及名稱跟著語系走', () => {
    document.cookie = 'trustforge_hermes_locale=en'
    renderPage()
    expect(screen.getByRole('switch', { name: 'Trust score drop alert' })).toBeInTheDocument()
    document.cookie = 'trustforge_hermes_locale=zh-TW'
  })
})
