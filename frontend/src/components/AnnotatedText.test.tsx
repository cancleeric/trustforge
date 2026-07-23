// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
})
