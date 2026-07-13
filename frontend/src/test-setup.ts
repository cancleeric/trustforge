import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// vitest.config.ts 未開 test.globals，@testing-library/react 的自動 afterEach
// cleanup 偵測不到全域 afterEach，這裡手動註冊，避免同一測試檔多個 it() 之間
// DOM 殘留導致 getByText 撞到前一個 render 留下的元素。
afterEach(() => {
  cleanup()
})
