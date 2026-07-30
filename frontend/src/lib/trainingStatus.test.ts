import { describe, expect, it } from 'vitest'
import { isTrainingStatusData } from './endpoints'

function validPayload() {
  return {
    training_data: {
      total_records: 2,
      has_direction: 1,
      direction_ratio: 0.5,
      per_coin: { BTC: { total: 2, has_direction: 1 } },
    },
    backfill: null,
    upgrade_threshold: { target: 100, current: 1, met: false, pct: 1 },
  }
}

describe('isTrainingStatusData', () => {
  it('accepts a complete and internally consistent payload', () => {
    expect(isTrainingStatusData(validPayload())).toBe(true)
  })

  it('accepts Python ties-to-even rounding at the four-decimal boundary', () => {
    const data = validPayload()
    data.training_data = {
      total_records: 32,
      has_direction: 1,
      direction_ratio: 0.0312,
      per_coin: { BTC: { total: 32, has_direction: 1 } },
    }
    data.upgrade_threshold = {
      target: 32,
      current: 1,
      met: false,
      pct: 3.1,
    }

    expect(isTrainingStatusData(data)).toBe(true)
  })

  it.each([
    ['fractional count', (data: ReturnType<typeof validPayload>) => { data.training_data.total_records = 2.5 }],
    ['out-of-range ratio', (data: ReturnType<typeof validPayload>) => { data.training_data.direction_ratio = 1.1 }],
    ['inexact derived ratio', (data: ReturnType<typeof validPayload>) => { data.training_data.direction_ratio = 0.5001 }],
    ['inconsistent per-coin total', (data: ReturnType<typeof validPayload>) => { data.training_data.per_coin.BTC.total = 1 }],
    ['inconsistent threshold', (data: ReturnType<typeof validPayload>) => { data.upgrade_threshold.current = 2 }],
    ['inexact derived percentage', (data: ReturnType<typeof validPayload>) => { data.upgrade_threshold.pct = 1.1 }],
  ])('rejects %s', (_label, mutate) => {
    const data = validPayload()
    mutate(data)
    expect(isTrainingStatusData(data)).toBe(false)
  })
})
