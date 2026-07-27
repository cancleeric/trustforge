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
    // 把平行 worker 數量壓低，用時間換取確定性：換來的是穩定但稍慢的 CI，而不是
    // 把逾時門檻整體調高去掩蓋 flake。
    // 2026-07-27 更正：上面那段 `poolOptions.threads.maxThreads` **從來沒有生效
    // 過**。Vitest 4 已移除 test.poolOptions（跑測試時會印 DEPRECATED
    // `test.poolOptions` was removed in Vitest 4），所有 pool 設定改成頂層。
    // 也就是說「壓到 2 個 worker 還是會 flake」這個觀察本身是假的——它根本還是
    // 開 8 個 worker 在跑。改用 Vitest 4 的頂層寫法：
    //   maxWorkers: 1 + fileParallelism: false → 完全序列化，一次只有一個 jsdom
    //   環境活著，記憶體峰值從 8 份降到 1 份，不再逼系統 swap。
    // testTimeout 一併從預設 5000 拉到 20000。拉逾時不是「調鬆門檻買綠燈」——
    // 逾時本身不是任何一條斷言，沒有任何 expect 因此變寬鬆；它只是在描述
    // 「這台機器多慢才算壞掉」，而這台機器（8GB、同時跑 40+ 容器）本來就會
    // 慢到 5 秒不夠。
    maxWorkers: 1,
    minWorkers: 1,
    fileParallelism: false,
    testTimeout: 20_000,
  },
})
