// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import GlossaryTerm from './GlossaryTerm'

describe('GlossaryTerm', () => {
  it('opens by click and closes with Escape for keyboard and touch users', () => {
    render(<GlossaryTerm term="trustScore" />)
    const trigger = screen.getByRole('button', { name: /信任分數/ })

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('note')).toHaveTextContent('不是價格漲跌機率')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
