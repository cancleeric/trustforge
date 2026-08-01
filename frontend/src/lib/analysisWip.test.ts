import { describe, expect, it } from 'vitest'

import { ANALYSIS_FORMAL_WIP } from './analysisWip'

describe('formal analysis release gate', () => {
  it('keeps the production report path enabled', () => {
    expect(ANALYSIS_FORMAL_WIP).toBe(false)
  })
})
