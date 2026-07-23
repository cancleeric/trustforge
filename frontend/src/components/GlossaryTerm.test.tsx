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
})
