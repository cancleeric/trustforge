// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { HermesI18nProvider, useHermesI18n, htmlLangFor } from './hermesI18n'

/**
 * N15：切成 EN 後介面文字已是英文，但 <html lang> 仍停在 zh-Hant，
 * 螢幕閱讀器會用中文語音念英文內容。lang 必須跟著當前語系走。
 */
function LocaleProbe() {
  const { locale, setLocale, t } = useHermesI18n()
  return (
    <div>
      <span data-testid="analyze-label">{t('analyze')}</span>
      <button type="button" onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')}>toggle</button>
    </div>
  )
}

describe('html lang syncs with HermesI18nProvider locale', () => {
  beforeEach(() => {
    document.cookie = 'trustforge_hermes_locale=; Max-Age=0; Path=/'
    document.documentElement.lang = 'zh-Hant'
  })

  it('maps locale to BCP 47 lang', () => {
    expect(htmlLangFor('zh-TW')).toBe('zh-Hant')
    expect(htmlLangFor('en')).toBe('en')
  })

  it('updates document.documentElement.lang when switching to English', () => {
    render(<HermesI18nProvider><LocaleProbe /></HermesI18nProvider>)
    expect(document.documentElement.lang).toBe('zh-Hant')

    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    // UI 文字轉英文的同時，lang 也必須轉成 en
    expect(screen.getByTestId('analyze-label').textContent).toBe('ANALYZE')
    expect(document.documentElement.lang).toBe('en')

    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    expect(document.documentElement.lang).toBe('zh-Hant')
  })
})
