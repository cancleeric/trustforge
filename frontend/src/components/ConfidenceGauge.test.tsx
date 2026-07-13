import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ConfidenceGauge from './ConfidenceGauge'
import type { DecisionState } from '../lib/types'

const TIER_LABEL = (label: string) => screen.getByLabelText(`信任等級：${label}`)

describe('ConfidenceGauge tierLabel', () => {
  it('normal 態依 calibratedConfidence 分桶：0.7→高(good)', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.7} rawConfidence={0.5} decisionState="normal" />,
    )
    const el = TIER_LABEL('高')
    expect(el.textContent).toBe('信任等級：高')
    expect(el).toHaveStyle({ color: 'var(--color-tf-good)' })
  })

  it('normal 態：calibratedConfidence=0.5→中(warn)，即便 rawConfidence 偏高也不影響', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.5} rawConfidence={0.95} decisionState="normal" />,
    )
    const el = TIER_LABEL('中')
    expect(el.textContent).toBe('信任等級：中')
    expect(el).toHaveStyle({ color: 'var(--color-tf-warn)' })
  })

  it('normal 態：calibratedConfidence=0.4→低(bad)', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.4} rawConfidence={0.95} decisionState="normal" />,
    )
    const el = TIER_LABEL('低')
    expect(el.textContent).toBe('信任等級：低')
    expect(el).toHaveStyle({ color: 'var(--color-tf-bad)' })
  })

  it('abstain 態：層標回「棄權／資料不足」(bad)、百分比仍顯示 hero=calibrated', () => {
    render(
      <ConfidenceGauge calibratedConfidence={0.8} rawConfidence={0.5} decisionState="abstain" />,
    )
    const el = TIER_LABEL('棄權／資料不足')
    expect(el).toHaveStyle({ color: 'var(--color-tf-bad)' })
    // 既有連續百分比仍保留（abstain 態 hero=calibratedConfidence=0.8→80%）
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('80%')
  })

  it('low_confidence 態：層標回「資訊完整度偏低」(warn)，百分比仍顯示', () => {
    render(
      <ConfidenceGauge
        calibratedConfidence={0.3}
        rawConfidence={0.5}
        decisionState="low_confidence"
      />,
    )
    const el = TIER_LABEL('資訊完整度偏低')
    expect(el).toHaveStyle({ color: 'var(--color-tf-warn)' })
    const ariaLabel = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(ariaLabel).toContain('30%')
  })
})

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
