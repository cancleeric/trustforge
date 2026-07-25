// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AnnotatedText from './AnnotatedText'
import { findGlossaryAnnotations } from '../lib/annotatedText'

describe('AnnotatedText', () => {
  it('annotates report glossary terms with accessible popovers', () => {
    render(<AnnotatedText text="FDV 與 TVL 同時上升，但 tokenomics 顯示解鎖賣壓。" />)

    const fdv = screen.getByRole('button', { name: /FDV/ })
    expect(fdv).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: /TVL/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /tokenomics/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /解鎖賣壓/ })).toBeInTheDocument()

    fireEvent.click(fdv)
    expect(fdv).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('note')).toHaveTextContent('Fully diluted valuation')
  })

  it('prefers longest non-overlapping matches and ignores popover-only terms', () => {
    const annotations = findGlossaryAnnotations('Trust Score 和資料時效都不是 report glossary，但 market cap 是。')

    expect(annotations).toHaveLength(1)
    expect(annotations[0]).toMatchObject({ term: 'market_cap' })
  })

  it('clamps popovers inside narrow viewports', () => {
    vi.stubGlobal('innerWidth', 240)
    render(<AnnotatedText text="TVL" />)
    const tvl = screen.getByRole('button', { name: /TVL/ })
    vi.spyOn(tvl.parentElement!, 'getBoundingClientRect').mockReturnValue({
      x: 220,
      y: 20,
      width: 32,
      height: 20,
      top: 20,
      right: 252,
      bottom: 40,
      left: 220,
      toJSON: () => ({}),
    } as DOMRect)

    fireEvent.click(tvl)

    const note = screen.getByRole('note')
    expect(note).toHaveStyle({ left: '12px', width: '216px' })
    vi.unstubAllGlobals()
  })
})
