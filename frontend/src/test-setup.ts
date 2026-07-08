import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// vitest.config.ts 未開 test.globals，@testing-library/react 的自動 afterEach
// cleanup 偵測不到全域 afterEach，這裡手動註冊，避免同一測試檔多個 it() 之間
// DOM 殘留導致 getByText 撞到前一個 render 留下的元素。
afterEach(() => {
  cleanup()
})

// recharts <ResponsiveContainer> 靠 ResizeObserver + getBoundingClientRect 量測
// 容器尺寸；jsdom 兩者皆不提供真實佈局，預設會停在 -1x-1（無效尺寸），導致
// <RadarChart> 底下所有子節點（含 <PolarRadiusAxis>）完全不渲染進 DOM，讓
// TrustRadarChart 的渲染測試變成偽陽性（沒炸只是因為什麼都沒畫）。這裡給一個
// 固定非零尺寸的最小 mock，讓圖表在測試環境下也能實際渲染出 SVG 節點。
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver

Element.prototype.getBoundingClientRect = () =>
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
  }) as DOMRect
