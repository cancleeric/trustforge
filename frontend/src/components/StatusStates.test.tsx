// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import { ErrorState } from './StatusStates'

function LocaleSwitcher({ to }: { to: 'en' | 'zh-TW' }) {
  const { setLocale } = useHermesI18n()
  setLocale(to)
  return null
}

function renderError(locale: 'en' | 'zh-TW', code: string, message: string) {
  return render(
    <HermesI18nProvider>
      <LocaleSwitcher to={locale} />
      <ErrorState code={code} message={message} />
    </HermesI18nProvider>,
  )
}

describe('ErrorState i18n (N3)', () => {
  // N3 regression guard: `message` is apiClient.ts's hard-coded zh-TW text
  // (see apiClient.ts's timeoutFailure/parseFailure/etc.) — it must never be
  // rendered verbatim, otherwise the error banner leaks Chinese text even
  // when the UI locale is switched to EN. The banner must instead localize
  // by `error.code`.
  it.each([
    ['timeout', '請求逾時（超過 30 秒無回應）'],
    ['parse_error', '伺服器回應非 JSON（HTTP 502）'],
    ['network_error', '網路連線失敗'],
    ['server_busy', '伺服器忙碌，請稍後再試'],
  ])('renders an English title/description for code=%s under the EN locale, never the raw zh-TW message', (code, rawMessage) => {
    renderError('en', code, rawMessage)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(code)
    expect(alert).not.toHaveTextContent(rawMessage)
    // sanity: some EN copy is actually present (not just blank)
    expect(alert.textContent?.trim().length).toBeGreaterThan(code.length)
  })

  it('still localizes to zh-TW copy (not the raw apiClient message) when locale is zh-TW', () => {
    renderError('zh-TW', 'timeout', '請求逾時（超過 30 秒無回應）')

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('回應逾時')
    // the raw apiClient message (with its specific "30 秒" wording) must not
    // leak through — only the mapped, locale-aware copy should render.
    expect(alert).not.toHaveTextContent('請求逾時（超過 30 秒無回應）')
  })

  it('falls back to a generic localized message for an unrecognized error code', () => {
    renderError('en', 'some_backend_specific_code', 'raw backend text')

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('some_backend_specific_code')
    expect(alert).toHaveTextContent('Service error')
    expect(alert).not.toHaveTextContent('raw backend text')
  })

  it('renders a non-clipped, fully labeled retry button (N1 companion check)', () => {
    renderError('zh-TW', 'network_error', 'x')

    const retry = screen.getByRole('button', { name: /重新嘗試/ })
    expect(retry).toHaveAttribute('title', '重試')
  })

  it('does not append the raw error code onto the title line (N5)', () => {
    // N5: the title paragraph previously read "Response timeout timeout"
    // (title text immediately followed by the raw `code`), which looks like
    // a stutter to end users. The code must not share the title's <p>.
    renderError('en', 'timeout', 'raw message')

    const alert = screen.getByRole('alert')
    const titleEl = Array.from(alert.querySelectorAll('p')).find((p) => p.textContent === 'Response timeout')
    expect(titleEl).toBeTruthy()
    // title text must not itself contain the code appended
    expect(titleEl!.textContent).not.toMatch(/timeout\s*timeout/i)
  })
})
