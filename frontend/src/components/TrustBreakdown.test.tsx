import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TrustBreakdown from './TrustBreakdown'
import type { TrustComponentsAggregate } from '../lib/types'

function getPercentText(label: string): string {
  const labelEl = screen.getByText(label)
  const flexDiv = labelEl.closest('div')
  const percentEl = flexDiv?.querySelector('.tf-num')
  return percentEl?.textContent ?? ''
}

const baseData: TrustComponentsAggregate = {
  reputation: 0.5,
  corroboration: 0.5,
  recency: 0.5,
  manipulation: 0.5,
}

describe('TrustBreakdown', () => {
  it('renders reputation as 0% when value is 0', () => {
    render(<TrustBreakdown data={{ ...baseData, reputation: 0 }} />)
    expect(getPercentText('信譽')).toBe('0%')
  })

  it('renders reputation as 50% when value is 0.5', () => {
    render(<TrustBreakdown data={{ ...baseData, reputation: 0.5 }} />)
    expect(getPercentText('信譽')).toBe('50%')
  })

  it('renders reputation as 100% when value is 1', () => {
    render(<TrustBreakdown data={{ ...baseData, reputation: 1 }} />)
    expect(getPercentText('信譽')).toBe('100%')
  })

  it('clamps reputation to 100% when value exceeds 1 and never shows 120%', () => {
    render(<TrustBreakdown data={{ ...baseData, reputation: 1.2 }} />)
    expect(getPercentText('信譽')).toBe('100%')
    expect(screen.queryByText('120%')).not.toBeInTheDocument()
  })

  it('clamps reputation to 0% when value is negative and never shows -10%', () => {
    render(<TrustBreakdown data={{ ...baseData, reputation: -0.1 }} />)
    expect(getPercentText('信譽')).toBe('0%')
    expect(screen.queryByText('-10%')).not.toBeInTheDocument()
  })

  it('falls back to 0% for manipulation when the field is missing', () => {
    const partial = {
      reputation: 0.5,
      corroboration: 0.5,
      recency: 0.5,
    } as unknown as TrustComponentsAggregate
    render(<TrustBreakdown data={partial} />)
    expect(getPercentText('操縱風險')).toBe('0%')
  })

  it('clamps every dimension within [0, 100]% when multiple fields are abnormal', () => {
    const data: TrustComponentsAggregate = {
      reputation: 1.2,
      corroboration: -0.1,
      recency: 2.5,
      manipulation: -3,
    }
    const { container } = render(<TrustBreakdown data={data} />)
    const text = container.textContent ?? ''
    const percents = [...text.matchAll(/(-?\d+)%/g)].map((m) => Number(m[1]))
    expect(percents.length).toBeGreaterThan(0)
    for (const p of percents) {
      expect(p).toBeGreaterThanOrEqual(0)
      expect(p).toBeLessThanOrEqual(100)
    }
  })
})
