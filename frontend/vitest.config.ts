import { defineConfig } from 'vitest/config'

// 純邏輯單元測試（apiClient/validators），不需要 DOM，跑 node 環境即可，
// 不引入 jsdom 額外依賴（credit-safe：僅 devDependency，不進 production
// bundle）。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
