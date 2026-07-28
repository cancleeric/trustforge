// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import QueryConsole from './QueryConsole'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import { defaultQuestionTypeForFocus } from '../lib/analysisTaxonomy'

function renderConsole(onSubmit = vi.fn()) {
  const utils = render(
    <MemoryRouter>
      <HermesI18nProvider>
        <QueryConsole
          initial={{ coin: 'BTC', type: 'multi_source', mode: 'risk', q: '測試題目' }}
          onSubmit={onSubmit}
        />
      </HermesI18nProvider>
    </MemoryRouter>,
  )
  return { ...utils, onSubmit }
}

/** N69：QueryConsole 不只是 /analyze 的表單——`hermes.css` 的
 * `@media (max-width:560px)` 會把 left-rail 整個 `display:none`，並刻意保留中央的
 * QueryConsole，所以**手機使用者唯一能打字的地方就是這張表單**。左軌那顆題型下拉
 * 拆掉時我第一版漏了這裡，等於只修好桌機。這組測試守住手機路徑。 */
describe('N69 QueryConsole 題型', () => {
  it('不提供官方題型三選一（那三種是主辦方的範例，不是可出題的範圍）', () => {
    const { container } = renderConsole()
    expect(container.querySelector('#qc-qtype')).toBeNull()
    const options = [...container.querySelectorAll('option')].map((o) => o.textContent?.trim())
    expect(options).not.toContain('多源整合')
    expect(options).not.toContain('假設驗證')
    // 負向對照的另一半：角度下拉與自由題目框必須都還在，否則這條會因為
    // 「整張表單消失」而假綠——手機沒有別的輸入口可以退。
    expect(container.querySelector('#qc-type')).not.toBeNull()
    expect(container.querySelector('#qc-q')).not.toBeNull()
  })

  it('送出時帶的 type 由分析角度推導，與後端 MODES 一致', () => {
    const { container, onSubmit } = renderConsole()
    // fundamentals 在後端是 HYPOTHESIS，跟預設的 risk→multi_source 不同，
    // 所以這個切換若沒生效，斷言會抓到還停在 multi_source。
    fireEvent.change(container.querySelector('#qc-type') as HTMLSelectElement, {
      target: { value: 'fundamentals' },
    })
    fireEvent.submit(container.querySelector('form') as HTMLFormElement)
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      mode: 'fundamentals',
      type: defaultQuestionTypeForFocus('fundamentals'),
    })
    expect(defaultQuestionTypeForFocus('fundamentals')).toBe('hypothesis')
  })
})

describe('#823 competition question picker', () => {
  it('fills exactly one deterministic question without submitting', () => {
    const onSubmit = vi.fn()
    const { container } = render(
      <MemoryRouter>
        <HermesI18nProvider>
          <QueryConsole
            initial={{ coin: 'BTC', type: 'multi_source', mode: 'risk', q: '原題目' }}
            onSubmit={onSubmit}
            random={() => 0}
          />
        </HermesI18nProvider>
      </MemoryRouter>,
    )
    const picker = screen.getByRole('button', { name: '隨機競賽題目' })
    fireEvent.click(picker)

    const textarea = container.querySelector('#qc-q') as HTMLTextAreaElement
    expect(textarea.value).toMatch(/^請分析 BTC：/)
    expect(textarea.value.split('\n')).toHaveLength(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
