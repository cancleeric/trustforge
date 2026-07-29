// @vitest-environment jsdom
/**
 * Tests for AgosBadge component.
 * Issue: #924 | Epic: #914
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AgosBadge, evidenceBadgeVariant, statusBadgeVariant } from './AgosBadge'

describe('AgosBadge', () => {
  it('renders label text', () => {
    render(<AgosBadge variant="historical" label="Context only" />)
    expect(screen.getByText('Context only')).toBeInTheDocument()
  })

  it('has aria-label', () => {
    render(<AgosBadge variant="trusted" label="Evidence" />)
    expect(screen.getByLabelText('Evidence')).toBeInTheDocument()
  })

  it('renders different variants without crashing', () => {
    const variants = [
      'historical', 'candidate', 'trusted', 'proposal',
      'risk-read', 'risk-local', 'risk-external', 'risk-deploy',
      'status-success', 'status-failed', 'status-pending', 'status-timeout',
    ] as const

    for (const variant of variants) {
      const { unmount } = render(<AgosBadge variant={variant} label={variant} />)
      expect(screen.getByText(variant)).toBeInTheDocument()
      unmount()
    }
  })
})

describe('evidenceBadgeVariant', () => {
  it('returns trusted for eligible', () => {
    expect(evidenceBadgeVariant(true)).toBe('trusted')
  })
  it('returns historical for non-eligible', () => {
    expect(evidenceBadgeVariant(false)).toBe('historical')
  })
})

describe('statusBadgeVariant', () => {
  it('maps success', () => expect(statusBadgeVariant('success')).toBe('status-success'))
  it('maps failed', () => expect(statusBadgeVariant('failed')).toBe('status-failed'))
  it('maps pending', () => expect(statusBadgeVariant('pending')).toBe('status-pending'))
  it('maps timeout', () => expect(statusBadgeVariant('timeout')).toBe('status-timeout'))
  it('maps unknown to historical', () => expect(statusBadgeVariant('unknown')).toBe('historical'))
})
