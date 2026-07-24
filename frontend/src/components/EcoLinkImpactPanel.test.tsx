// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import EcoLinkImpactPanel from './EcoLinkImpactPanel'
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

describe('EcoLinkImpactPanel', () => {
  it('verdict: possible_relation 時渲染影響路徑、confidence 與官方來源連結', () => {
    render(
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
    render(<EcoLinkImpactPanel verdict="insufficient_data" message="資料不足，無法判定" impactPaths={[]} />)
    expect(screen.getByText('資料不足，無法判定。')).toBeInTheDocument()
    expect(screen.queryByTestId('impact-path')).not.toBeInTheDocument()
  })

  it('渲染「示範資料」illustrative 揭露徽章（possible_relation 與 insufficient_data 都要顯示）', () => {
    const { unmount } = render(
      <EcoLinkImpactPanel verdict="possible_relation" message="可能相關" impactPaths={[makePath()]} />,
    )
    expect(screen.getByText(/示範資料/)).toBeInTheDocument()
    unmount()
    render(<EcoLinkImpactPanel verdict="insufficient_data" message="資料不足，無法判定" impactPaths={[]} />)
    expect(screen.getByText(/示範資料/)).toBeInTheDocument()
  })

  it('official_source_url 為 javascript: scheme 時不渲染成可點連結（safeHref 擋 XSS）', () => {
    render(
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
    const { container } = render(
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
