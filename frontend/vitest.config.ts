import { defineConfig } from 'vitest/config'

// 除了純邏輯單元測試（apiClient/validators）外，元件渲染測試（TrustBreakdown/
// ConfidenceGauge/TrustRadarChart）需要 DOM，統一用 jsdom 環境（jsdom 純
// devDependency，不進 production bundle，credit-safe）。include 涵蓋 .test.ts
// 與 .test.tsx（元件測試用 .tsx 才能寫 JSX）。
export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
