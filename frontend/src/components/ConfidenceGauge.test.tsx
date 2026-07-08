import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ConfidenceGauge from './ConfidenceGauge'
import type { DecisionState } from '../lib/types'

describe('ConfidenceGauge', () => {
  it('normal 狀態下 rawConfidence=0 顯示 0%', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={0} decisionState="normal" />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('0%')
    expect(ariaLabel).toContain('信任分')
  })

  it('normal 狀態下 rawConfidence=0.5 顯示 50%', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={0.5} decisionState="normal" />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('50%')
  })

  it('normal 狀態下 rawConfidence=1 顯示 100%', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={1} decisionState="normal" />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('100%')
  })

  it('normal 狀態下 rawConfidence=1.2 超過上限應 clamp 至 100%，不顯示 120%', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={1.2} decisionState="normal" />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('100%')
    expect(ariaLabel).not.toContain('120%')
  })

  it('normal 狀態下 rawConfidence=-0.1 為負數應 clamp 至 0%，不顯示 -10%', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={-0.1} decisionState="normal" />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('0%')
    expect(ariaLabel).not.toContain('-10%')
  })

  it('abstain 狀態下改用 calibratedConfidence 當 hero，異常值 1.5 應 clamp 至 100%，不顯示 150%', () => {
    render(
      <ConfidenceGauge
        calibratedConfidence={1.5}
        rawConfidence={0.5}
        decisionState="abstain"
      />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('100%')
    expect(ariaLabel).not.toContain('150%')
    expect(ariaLabel).toContain('資訊完整度')
  })

  it('未知 legacy decisionState 值應 fallback 成 normal，hero 使用 rawConfidence', () => {
    render(
      <ConfidenceGauge
        calibratedConfidence={0.2}
        rawConfidence={0.7}
        decisionState={'unknown_legacy_value' as unknown as DecisionState}
      />,
    )
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('70%')
    expect(ariaLabel).toContain('信任分')
  })
})
