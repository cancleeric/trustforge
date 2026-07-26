// @vitest-environment jsdom
// N11：EN 模式送出的分析請求必須把語系帶到後端。
// 斷言的是「實際打出去的 fetch body」，不是原始碼字串。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { currentNarrativeLocale, registerAnalysisQuestion } from './endpoints'

function okResponse() {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve({
      ok: true,
      data: { question_id: 'question-1', job_id: 'flow-1', state: 'queued', origin: 'manual' },
    }),
  }
}

function setLocaleCookie(value: string | null) {
  if (value === null) {
    document.cookie = 'trustforge_hermes_locale=; Max-Age=0; Path=/'
    return
  }
  document.cookie = `trustforge_hermes_locale=${value}; Path=/`
}

async function sentBody(): Promise<Record<string, unknown>> {
  const fetchMock = vi.fn().mockResolvedValue(okResponse())
  vi.stubGlobal('fetch', fetchMock)
  await registerAnalysisQuestion('BTC', 'multi_source', 'state?')
  const init = fetchMock.mock.calls[0][1] as RequestInit
  return JSON.parse(String(init.body)) as Record<string, unknown>
}

afterEach(() => {
  vi.unstubAllGlobals()
  setLocaleCookie(null)
})

describe('registerAnalysisQuestion — narrative locale (N11)', () => {
  it('英文介面（cookie=en）→ body 帶 locale: en', async () => {
    setLocaleCookie('en')
    expect(await sentBody()).toMatchObject({ coin: 'BTC', mode: 'multi_source', locale: 'en' })
  })

  it('中文介面（cookie=zh-TW）→ body 帶後端契約值 zh-Hant', async () => {
    setLocaleCookie('zh-TW')
    expect(await sentBody()).toMatchObject({ locale: 'zh-Hant' })
  })

  it('沒有 cookie → 預設 zh-Hant', async () => {
    expect(await sentBody()).toMatchObject({ locale: 'zh-Hant' })
  })

  it('顯式 locale 參數優先於 cookie', async () => {
    setLocaleCookie('zh-TW')
    const fetchMock = vi.fn().mockResolvedValue(okResponse())
    vi.stubGlobal('fetch', fetchMock)
    await registerAnalysisQuestion('BTC', 'multi_source', 'state?', undefined, 'en')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toMatchObject({ locale: 'en' })
  })

  it('currentNarrativeLocale 反映 cookie', () => {
    setLocaleCookie('en')
    expect(currentNarrativeLocale()).toBe('en')
    setLocaleCookie('zh-TW')
    expect(currentNarrativeLocale()).toBe('zh-Hant')
  })
})
