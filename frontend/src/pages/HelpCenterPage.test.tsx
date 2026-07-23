// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import HelpCenterPage from './HelpCenterPage'
import { HELP_CENTER_GLOSSARY } from '../lib/glossaryCatalog'

describe('HelpCenterPage', () => {
  it('renders glossary rows from the shared catalog', () => {
    render(<HelpCenterPage />)

    const fdv = HELP_CENTER_GLOSSARY.find((term) => term.term_id === 'fdv')
    expect(fdv).toBeTruthy()
    expect(screen.getByText('FDV')).toBeInTheDocument()
    expect(screen.getByText(fdv!.description)).toBeInTheDocument()
  })
})
