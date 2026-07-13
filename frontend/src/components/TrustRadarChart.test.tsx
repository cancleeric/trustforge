// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import TrustRadarChart from './TrustRadarChart'
import type { TrustRadar, TrustRadarDimension } from '../lib/types'

function makeDim(overrides: Partial<TrustRadarDimension> = {}): TrustRadarDimension {
  return {
    label: '價格',
    has_data: true,
    trust: 0.7,
    n_sources: 2,
    n_evidence: 5,
    single_source: false,
    ...overrides,
  }
}

describe('TrustRadarChart', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    Element.prototype.getBoundingClientRect = vi.fn(
      () =>
        ({
          width: 400,
          height: 300,
          top: 0,
          left: 0,
          bottom: 300,
          right: 400,
          x: 0,
          y: 0,
          toJSON() {},
        }) as DOMRect,
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    Element.prototype.getBoundingClientRect = vi.fn(
      () =>
        ({
          width: 0,
          height: 0,
          top: 0,
          left: 0,
          bottom: 0,
          right: 0,
          x: 0,
          y: 0,
          toJSON() {},
        }) as DOMRect,
    )
  })

  it('多維度有資料時應正常渲染且包含 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', trust: 0.8 }),
      onchain: makeDim({ label: '鏈上', trust: 0.5 }),
      news: makeDim({ label: '新聞', trust: 0.3 }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(container.querySelector('.recharts-polar-radius-axis')).not.toBeNull()
  })

  it('所有維度皆無資料時應顯示 fallback 文字且不渲染 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', has_data: false, trust: null }),
      onchain: makeDim({ label: '鏈上', has_data: false, trust: null }),
      news: makeDim({ label: '新聞', has_data: false, trust: null }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(
      screen.getByText('目前尚無任何維度累積足夠資料，暫無法繪製雷達圖。')
    ).toBeDefined()
    expect(container.querySelector('.recharts-polar-radius-axis')).toBeNull()
  })

  it('單一維度有資料時應正常渲染且包含 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', trust: 0.8 }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(container.querySelector('.recharts-polar-radius-axis')).not.toBeNull()
  })

  // issue #106 D0.4 三態誠實合約「未評估 ≠ 零」：部分維度缺資料時，該維度
  // 必須顯式列在「尚無資料的維度」清單、且**不**被當成 0 分畫進雷達圖
  // （domain=[0,1] 只承載真實評分維度）。
  it('部分維度缺資料時應列出未評估維度、且不得把缺資料維度當 0 分畫入雷達', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', trust: 0.8 }),
      onchain: makeDim({ label: '鏈上', has_data: false, trust: null }),
      news: makeDim({ label: '新聞', has_data: false, trust: null }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    // 缺資料維度必須顯式標示（不得消失、不得冒充 0 分）
    expect(screen.getByText(/尚無資料的維度：鏈上、新聞/)).toBeDefined()
    // 雷達圖仍正常渲染（domain=[0,1] 只承載真實評分維度）
    expect(container.querySelector('.recharts-polar-radius-axis')).not.toBeNull()
    // 缺資料維度不會被補 0 混進圖表數據
    expect(container.textContent).not.toMatch(/鏈上.*0\.00|新聞.*0\.00/)
  })
})
