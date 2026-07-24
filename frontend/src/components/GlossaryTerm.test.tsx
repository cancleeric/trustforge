// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import GlossaryTerm from './GlossaryTerm'
import { GLOSSARY_BY_ID, HELP_CENTER_GLOSSARY } from '../lib/glossaryCatalog'

describe('GlossaryTerm', () => {
  it('opens by click and closes with Escape for keyboard touch users', () => {
    render(<GlossaryTerm term="trustScore" />)
    const trigger = screen.getByRole('button', { name: /信任分數/ })

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('note')).toHaveTextContent('不是價格漲跌機率')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('uses the shared glossary catalog for popovers and help center entries', () => {
    render(<GlossaryTerm term="fdv" />)

    expect(screen.getByRole('button', { name: /FDV/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /FDV/ }))
    expect(screen.getByRole('note')).toHaveTextContent(GLOSSARY_BY_ID.fdv.description)
    expect(HELP_CENTER_GLOSSARY.some((term) => term.term_id === 'fdv')).toBe(true)
  })

  it('preserves approved score-axis popover semantics', () => {
    expect(GLOSSARY_BY_ID.recency.description).toContain('獨立 0-100 子分數')
    expect(GLOSSARY_BY_ID.manipulation.label).toBe('抗操縱能力')
    expect(GLOSSARY_BY_ID.manipulation.description).toContain('喊單、誇大承諾與協同行為')
    expect(GLOSSARY_BY_ID.divergence.label).toBe('跨來源分歧')
    expect(GLOSSARY_BY_ID.divergence.description).toContain('結論越需要保守解讀')
  })

  it('shows a ⚠️ risk note for terms that have one', () => {
    render(<GlossaryTerm term="tvl" />)
    fireEvent.click(screen.getByRole('button', { name: /TVL/ }))

    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('⚠️')
    expect(note).toHaveTextContent(GLOSSARY_BY_ID.tvl.riskNote as string)
  })

  it('does not render a risk note block for terms without one', () => {
    render(<GlossaryTerm term="market_cap" />)
    fireEvent.click(screen.getByRole('button', { name: /MC/ }))

    expect(GLOSSARY_BY_ID.market_cap.riskNote).toBeUndefined()
    expect(screen.getByRole('note')).not.toHaveTextContent('⚠️')
  })
})
