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
      data: {
        schema_version: 'formal-run-receipt/v1',
        receipt_id: 'frc_1',
        question_id: 'question-1',
        job_id: 'flow-1',
        result_id: 'result-1',
        state: 'accepted',
        origin: 'manual',
        disposition: 'created',
        locale: 'zh-Hant',
        created_at: '2026-07-30T08:00:00Z',
        expires_at: null,
        fingerprint_version: 'analysis-question/v1',
      },
    }),
  }
}

function errorResponse(status: number, code: string) {
  return {
    ok: false,
    status,
    json: () => Promise.resolve({
      ok: false,
      error: { code, message: code },
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
  await registerAnalysisQuestion('BTC', 'multi_source', 'state?', 'tf1.202607.AAECAwQFBgcICQoLDA0ODw', false)
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
    await registerAnalysisQuestion('BTC', 'multi_source', 'state?', 'tf1.202607.AAECAwQFBgcICQoLDA0ODw', false, undefined, 'en')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toMatchObject({ locale: 'en' })
  })

  it('sends the formal key header and explicit fresh flag', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse())
    vi.stubGlobal('fetch', fetchMock)
    await registerAnalysisQuestion('BTC', 'risk', 'state?', 'tf1.202607.AAECAwQFBgcICQoLDA0ODw', true)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.headers).toMatchObject({
      'Idempotency-Key': 'tf1.202607.AAECAwQFBgcICQoLDA0ODw',
      'Content-Type': 'application/json',
    })
    expect(JSON.parse(String(init.body))).toMatchObject({ fresh: true })
  })

  it('retries one caller-scope challenge with the exact same formal key and body', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(errorResponse(428, 'caller_scope_required'))
      .mockResolvedValueOnce(okResponse())
    vi.stubGlobal('fetch', fetchMock)

    const result = await registerAnalysisQuestion(
      'BTC',
      'risk',
      'state?',
      'tf1.202607.AAECAwQFBgcICQoLDA0ODw',
      true,
    )

    expect(result.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const first = fetchMock.mock.calls[0][1] as RequestInit
    const retry = fetchMock.mock.calls[1][1] as RequestInit
    expect(retry.headers).toEqual(first.headers)
    expect(retry.body).toBe(first.body)
  })

  it('surfaces a second caller-scope challenge without retrying forever', async () => {
    const fetchMock = vi.fn().mockResolvedValue(errorResponse(428, 'caller_scope_required'))
    vi.stubGlobal('fetch', fetchMock)

    const result = await registerAnalysisQuestion(
      'BTC',
      'risk',
      'state?',
      'tf1.202607.AAECAwQFBgcICQoLDA0ODw',
      false,
    )

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result).toEqual({
      ok: false,
      error: { code: 'caller_scope_required', message: 'caller_scope_required' },
    })
  })

  it('does not invent a new key after a response-loss network error', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('connection lost'))
      .mockResolvedValueOnce(okResponse())
    vi.stubGlobal('fetch', fetchMock)
    const key = 'tf1.202607.AAECAwQFBgcICQoLDA0ODw'

    const lost = await registerAnalysisQuestion('BTC', 'risk', 'state?', key, false)
    const replay = await registerAnalysisQuestion('BTC', 'risk', 'state?', key, false)

    expect(lost).toMatchObject({ ok: false, error: { code: 'network_error' } })
    expect(replay.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    for (const call of fetchMock.mock.calls) {
      expect((call[1] as RequestInit).headers).toMatchObject({ 'Idempotency-Key': key })
    }
  })

  it('currentNarrativeLocale 反映 cookie', () => {
    setLocaleCookie('en')
    expect(currentNarrativeLocale()).toBe('en')
    setLocaleCookie('zh-TW')
    expect(currentNarrativeLocale()).toBe('zh-Hant')
  })
})
