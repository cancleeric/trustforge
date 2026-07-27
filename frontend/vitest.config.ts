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
    // 開發機是 8GB RAM 的共用機器（同時跑 40+ Docker 容器），55 個測試檔預設會
    // 開到 os.cpus().length（8）個 worker thread 平行跑，每個 jsdom + React 環境
    // 都吃記憶體，滿載時把可用記憶體榨乾到只剩幾十 MB free，逼系統狂 swap。
    // 這不是測試邏輯的 bug：連一個已 resolve 的 mock promise 的 .then 都會被
    // swap I/O 卡到 1 秒以上，導致 waitFor()／testTimeout 隨機炸裂
    // （見 HermesDashboard.test.tsx N2、hermesLayoutContract.test.ts N6 的隨機
    // 逾時，兩者互不相關卻同天 5000ms 逾時，指向環境資源，不是個別測試邏輯）。
    // v0.26 降至 2 workers 仍不足以完全消除；v0.27 進一步降為 single worker＋
    // 逾時門檻從預設 5000ms 調到 15000ms：在這台共用機上，單一 worker 可避免
    // jsdom 競爭，15s 足夠覆蓋最極端的 swap I/O spike。
    testTimeout: 30000,
    poolOptions: {
      threads: {
        minThreads: 1,
        maxThreads: 1,
      },
    },
  },
})
