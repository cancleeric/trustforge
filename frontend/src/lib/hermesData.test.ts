import { describe, expect, it } from 'vitest'
import { buildGalaxyModel, GALAXY_IDENTITIES } from './hermesData'
import type { OverviewData } from './types'

describe('Hermes competition galaxy', () => {
  it('contains exactly the official five-coin pool', () => {
    expect(GALAXY_IDENTITIES.map((coin) => coin.name)).toEqual(['BTC', 'ETH', 'SOL', 'BNB', 'XRP'])
    expect(GALAXY_IDENTITIES.find((coin) => coin.orbit === 'core')?.name).toBe('BTC')
  })

  it('maps overview scores by coin without leaking fiat design placeholders', () => {
    const overview = {
      coins: [
        { coin: 'BTC', trust_score: 0.61, manip_score: 0.1 },
        { coin: 'ETH', trust_score: 0.58, manip_score: 0.2 },
      ],
    } as OverviewData
    const model = buildGalaxyModel(overview)
    expect(model.byId.btc.score).toBe(61)
    expect(model.byId.eth.score).toBe(58)
    expect(model.coins).toHaveLength(5)
    expect(model.byId.usd).toBeUndefined()
  })
})
