import { defineConfig } from 'vitest/config'

// 預設 node 環境（純邏輯單元測試 apiClient/validators 不需要 DOM）。
// 需要 DOM 的元件測試（components 下）改用檔頭 docblock `// @vitest-environment
// jsdom` 指定（Vitest 4 已移除 test.environmentMatchGlobs 與 workspace 多
// project，docblock 為等價寫法）。jsdom 為純 devDependency，不進 production
// bundle，credit-safe。setupFiles 只保留 afterEach cleanup，全域 ResizeObserver
// / getBoundingClientRect mock 已收斂到 TrustRadarChart.test.tsx。
export default defineConfig({
  test: {
    environment: 'node',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
