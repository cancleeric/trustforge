import { describe, expect, it } from 'vitest'
import { formatTimestamp } from './format'

describe('formatTimestamp', () => {
  it('renders an ISO timestamp as a compact local timestamp', () => {
    expect(formatTimestamp('2026-07-13T15:20:28Z')).toMatch(/^07\/\d{2} \d{2}:\d{2}$/)
  })

  it('returns an em dash for absent or malformed timestamps', () => {
    expect(formatTimestamp(null)).toBe('—')
    expect(formatTimestamp('not-a-timestamp')).toBe('—')
  })
})
