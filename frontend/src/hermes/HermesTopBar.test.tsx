// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import HermesTopBar from './HermesTopBar'

describe('HermesTopBar', () => {
  it('exposes every operational workspace from the flagship dashboard', () => {
    render(
      <MemoryRouter>
        <HermesTopBar version="v0.test · GALAXY" costLedger={1.25} />
      </MemoryRouter>,
    )
    expect(screen.getByText('v0.test · GALAXY')).toBeInTheDocument()
    expect(screen.getByText('$1.2500')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'ANALYZE' })).toHaveAttribute('href', '/analyze')
    expect(screen.getByRole('link', { name: 'COMPARE' })).toHaveAttribute('href', '/compare')
    expect(screen.getByRole('link', { name: 'HISTORY' })).toHaveAttribute('href', '/history')
    expect(screen.getByRole('link', { name: 'SOURCES' })).toHaveAttribute('href', '/status')
    expect(screen.getByRole('link', { name: 'COSTS' })).toHaveAttribute('href', '/costs')
  })
})
