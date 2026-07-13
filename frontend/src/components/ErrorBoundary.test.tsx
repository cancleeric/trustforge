import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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

  it('子元件拋錯時顯示品牌化 fallback 而非空白頁，且提供回首頁連結', () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>
      </MemoryRouter>,
    )
    expect(screen.getByText('頁面發生未預期的錯誤')).toBeInTheDocument()
    const home = screen.getByRole('link', { name: '回首頁' })
    expect(home).toHaveAttribute('href', '/')
  })
})
