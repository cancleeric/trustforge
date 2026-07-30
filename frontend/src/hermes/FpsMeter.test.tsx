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
