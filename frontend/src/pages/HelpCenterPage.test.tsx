// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import HelpCenterPage from './HelpCenterPage'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import { HELP_CENTER_GLOSSARY } from '../lib/glossaryCatalog'

describe('HelpCenterPage', () => {
  it('renders glossary rows from the shared catalog', () => {
    // N58：頁面文案改走 i18n 之後需要 provider（同 AssetContextLookupPage.test）。
    render(<HermesI18nProvider><HelpCenterPage /></HermesI18nProvider>)

    const fdv = HELP_CENTER_GLOSSARY.find((term) => term.term_id === 'fdv')
    expect(fdv).toBeTruthy()
    expect(screen.getByText('FDV')).toBeInTheDocument()
    expect(screen.getByText(fdv!.description)).toBeInTheDocument()
  })

  it('N58: 頁面框架文案跟著語系走，不再硬寫中文', () => {
    document.cookie = 'trustforge_hermes_locale=en'
    render(<HermesI18nProvider><HelpCenterPage /></HermesI18nProvider>)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Help center')
    expect(screen.getByText('In plain language')).toBeInTheDocument()
    expect(screen.queryByText('白話說明')).not.toBeInTheDocument()
    document.cookie = 'trustforge_hermes_locale=zh-TW'
  })
})
