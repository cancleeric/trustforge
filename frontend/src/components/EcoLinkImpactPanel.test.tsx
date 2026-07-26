// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import EcoLinkImpactPanel from './EcoLinkImpactPanel'
import { HermesI18nProvider, type HermesLocale } from '../hermes/hermesI18n'
import type { EcoLinkImpactPath } from '../lib/types'

function makePath(overrides: Partial<EcoLinkImpactPath> = {}): EcoLinkImpactPath {
  return {
    event_id: 'upgrade:arb:stylus',
    path: ['asset:arb', 'asset:eth'],
    direction: 'mixed',
    confidence: 0.85,
    official_source_url: 'https://arbitrum.foundation/upgrade/stylus',
    ...overrides,
  }
}

function renderWithLocale(ui: React.ReactElement, locale: HermesLocale = 'zh-TW') {
  document.cookie = `trustforge_hermes_locale=${locale}; Path=/`
  return render(<HermesI18nProvider>{ui}</HermesI18nProvider>)
}

describe('EcoLinkImpactPanel', () => {
  it('verdict: possible_relation 時渲染影響路徑、confidence 與官方來源連結', () => {
    renderWithLocale(
      <EcoLinkImpactPanel
        verdict="possible_relation"
        message="asset:arb 與 asset:eth 可能相關"
        impactPaths={[makePath()]}
      />,
    )
    expect(screen.getByText(/可能相關/)).toBeInTheDocument()
    expect(screen.getByText('asset:arb → asset:eth')).toBeInTheDocument()
    expect(screen.getByText('confidence 85%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '官方來源' })).toHaveAttribute(
      'href',
      'https://arbitrum.foundation/upgrade/stylus',
    )
  })

  it('verdict: insufficient_data 時顯示「資料不足，無法判定」，不假裝沒有影響', () => {
    renderWithLocale(<EcoLinkImpactPanel verdict="insufficient_data" message="資料不足，無法判定" impactPaths={[]} />)
    expect(screen.getByText('資料不足，無法判定。')).toBeInTheDocument()
    expect(screen.queryByTestId('impact-path')).not.toBeInTheDocument()
  })

  it('渲染「示範資料」illustrative 揭露徽章（zh-TW，possible_relation 與 insufficient_data 都要顯示）', () => {
    const { unmount } = renderWithLocale(
      <EcoLinkImpactPanel verdict="possible_relation" message="可能相關" impactPaths={[makePath()]} />,
      'zh-TW',
    )
    expect(screen.getByText(/示範資料/)).toBeInTheDocument()
    unmount()
    renderWithLocale(
      <EcoLinkImpactPanel verdict="insufficient_data" message="資料不足，無法判定" impactPaths={[]} />,
      'zh-TW',
    )
    expect(screen.getByText(/示範資料/)).toBeInTheDocument()
  })

  it('顯示 illustrative 揭露徽章（en，內容需為英文，不殘留中文；possible_relation 與 insufficient_data 都要顯示）', () => {
    const { unmount } = renderWithLocale(
      <EcoLinkImpactPanel verdict="possible_relation" message="asset:arb, asset:eth possibly related" impactPaths={[makePath()]} />,
      'en',
    )
    expect(screen.getByText(/Illustrative data/)).toBeInTheDocument()
    expect(screen.queryByText(/示範資料/)).not.toBeInTheDocument()
    unmount()
    renderWithLocale(
      <EcoLinkImpactPanel verdict="insufficient_data" message="Insufficient data to determine" impactPaths={[]} />,
      'en',
    )
    expect(screen.getByText(/Illustrative data/)).toBeInTheDocument()
    expect(screen.queryByText(/示範資料/)).not.toBeInTheDocument()
  })

  it('official_source_url 為 javascript: scheme 時不渲染成可點連結（safeHref 擋 XSS）', () => {
    renderWithLocale(
      <EcoLinkImpactPanel
        verdict="possible_relation"
        message="可能相關"
        impactPaths={[makePath({ official_source_url: 'javascript:alert(1)' })]}
      />,
    )
    expect(screen.queryByRole('link', { name: '官方來源' })).not.toBeInTheDocument()
    expect(screen.getByText(/官方來源（連結格式無效）/)).toBeInTheDocument()
  })

  it('文案不得出現「導致」「因此」等因果字眼', () => {
    const { container } = renderWithLocale(
      <EcoLinkImpactPanel
        verdict="possible_relation"
        message="asset:arb 與 asset:eth 可能相關"
        impactPaths={[makePath()]}
      />,
    )
    expect(container.textContent).not.toMatch(/導致/)
    expect(container.textContent).not.toMatch(/因此/)
  })
})
