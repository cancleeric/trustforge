// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import FpsMeter from './FpsMeter'

describe('FpsMeter', () => {
  it('exposes FPS and adaptive quality while using the stable HUD class', () => {
    render(<FpsMeter fps={58} quality="high" measuring={false} />)

    const hud = screen.getByRole('img', { name: '58 FPS · HIGH' })
    expect(hud).toHaveClass('hermes-fps-meter')
    expect(screen.getByText('HIGH')).toHaveAttribute('data-short', 'H')
    expect(hud).not.toHaveAttribute('style')
  })

  it('labels the measuring state without a live region and supplies a compact mobile glyph', () => {
    render(<FpsMeter fps={24} quality="low" measuring />)

    expect(screen.getByRole('img', { name: '24 FPS · DETECTING…' })).toBeInTheDocument()
    expect(screen.getByText('DETECTING…')).toHaveAttribute('data-short', '…')
  })

  it('uses caller-provided localized quality labels', () => {
    render(<FpsMeter fps={60} quality="high" measuring={false} labels={{
      high: '高畫質',
      medium: '中畫質',
      low: '低畫質',
      detecting: '偵測畫質中…',
    }} />)

    expect(screen.getByRole('img', { name: '60 FPS · 高畫質' })).toBeInTheDocument()
    expect(screen.getByText('高畫質')).toBeInTheDocument()
  })
})

describe('FpsMeter positioning (CSS contract)', () => {
  it('applies hermes-fps-meter class which positions the HUD at top-right', async () => {
    // This test verifies the CSS contract: the component uses the stable
    // class name that hermes.css positions at top-right (fixed, top:12px, right:12px).
    // Actual visual position is validated via the stylesheet, not inline styles.
    render(<FpsMeter fps={50} quality="medium" measuring={false} labels={{
      high: '高畫質',
      medium: '中畫質',
      low: '低畫質',
      detecting: '偵測畫質中…',
    }} />)

    const meter = screen.getByRole('img', { name: '50 FPS · 中畫質' })
    expect(meter).toHaveClass('hermes-fps-meter')
    // Ensure no inline positioning styles that could override CSS top-right placement
    expect(meter).not.toHaveAttribute('style')
  })

  it('does not use inline bottom/left styles that would conflict with top-right CSS', () => {
    render(<FpsMeter fps={30} quality="low" measuring={false} />)

    const meter = screen.getByRole('img', { name: '30 FPS · LOW' })
    const style = meter.getAttribute('style')
    // No inline style at all, or at minimum no bottom/left positioning
    if (style) {
      expect(style).not.toMatch(/bottom/i)
      expect(style).not.toMatch(/\bleft\b/i)
    }
  })
})
