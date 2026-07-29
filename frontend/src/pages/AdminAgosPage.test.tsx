// @vitest-environment jsdom
/**
 * Tests for AdminAgosPage.
 * Issue: #924 | Epic: #914
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AdminAgosPage from './AdminAgosPage'

describe('AdminAgosPage', () => {
  it('renders page title', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Agent OS Admin')).toBeInTheDocument()
  })

  it('renders all 4 tabs', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Memory')).toBeInTheDocument()
    expect(screen.getByText('Skills')).toBeInTheDocument()
    expect(screen.getByText('Tools')).toBeInTheDocument()
    expect(screen.getByText('Context')).toBeInTheDocument()
  })

  it('renders run_id input', () => {
    render(<AdminAgosPage />)
    expect(screen.getByLabelText('Run ID')).toBeInTheDocument()
  })

  it('renders query button', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Query')).toBeInTheDocument()
  })

  it('query button is disabled when input empty', () => {
    render(<AdminAgosPage />)
    const btn = screen.getByText('Query')
    expect(btn).toBeDisabled()
  })
})
