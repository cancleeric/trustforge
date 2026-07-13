import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Link, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ErrorBoundary from './ErrorBoundary'

function Boom(): never {
  throw new Error('boom')
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    // 抑制 React 對 boundary 攔截錯誤的預期 console.error 噪音（含我方的觀測 log）。
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('正常子樹照常渲染', () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <p>正常內容</p>
        </ErrorBoundary>
      </MemoryRouter>,
    )
    expect(screen.getByText('正常內容')).toBeInTheDocument()
  })

  it('子元件拋錯時顯示品牌化 fallback、移除原內容，且提供回首頁連結', () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <Boom />
          <p>正常內容</p>
        </ErrorBoundary>
      </MemoryRouter>,
    )
    expect(screen.getByText('頁面發生未預期的錯誤')).toBeInTheDocument()
    // 反向斷言：fallback 取代整個子樹，原內容不得殘留（守住「假測試」）。
    expect(screen.queryByText('正常內容')).not.toBeInTheDocument()
    const home = screen.getByRole('link', { name: '回首頁' })
    expect(home).toHaveAttribute('href', '/')
  })

  // codex 對抗審 HIGH：驗證「錯誤狀態隨路由切換 reset」的修正機制。
  // 模擬 App.tsx 的 `key={location.pathname}` 接線：只在 /bad 路由拋錯，
  // 導航到 /good 後 boundary 應重掛、自動清錯誤、顯示新頁（非卡 fallback）。
  it('key 綁 location：路由切換後自動 reset，不卡 fallback', () => {
    function RoutedContent() {
      const location = useLocation()
      return (
        <ErrorBoundary key={location.pathname}>
          <Routes>
            <Route path="/bad" element={<Boom />} />
            <Route path="/good" element={<p>安全頁</p>} />
          </Routes>
        </ErrorBoundary>
      )
    }

    render(
      <MemoryRouter initialEntries={['/bad']}>
        {/* 導覽刻意放在 boundary 之外（等同 App 的 Header），驗證切換能逃離 fallback */}
        <Link to="/good">去安全頁</Link>
        <RoutedContent />
      </MemoryRouter>,
    )

    // 一開始在 /bad → 顯示 fallback
    expect(screen.getByText('頁面發生未預期的錯誤')).toBeInTheDocument()

    // 點導覽切到 /good → key 變動、boundary 重掛、錯誤清除
    fireEvent.click(screen.getByRole('link', { name: '去安全頁' }))
    expect(screen.getByText('安全頁')).toBeInTheDocument()
    expect(screen.queryByText('頁面發生未預期的錯誤')).not.toBeInTheDocument()
  })
})
