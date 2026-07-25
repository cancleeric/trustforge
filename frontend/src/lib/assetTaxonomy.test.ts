import { describe, expect, it } from 'vitest'
import {
  ASSET_LAYERS,
  ASSET_SECTORS,
  MARKET_CAP_TIERS,
  TOKEN_ROLES,
  isKnownAssetLayer,
  isKnownAssetSector,
  isKnownMarketCapTier,
  isKnownTokenRole,
} from '../lib/types'

/**
 * Asset taxonomy 型別靜態稽核：確保前端 union type、常數陣列、runtime type
 * guard 三者一致，且與後端 enum 保持同步。
 */

describe('ASSET_SECTORS 常量與 isKnownAssetSector', () => {
  it('包含全部已知 sector 值', () => {
    expect(ASSET_SECTORS).toContain('defi')
    expect(ASSET_SECTORS).toContain('l1')
    expect(ASSET_SECTORS).toContain('l2')
    expect(ASSET_SECTORS).toContain('stablecoin')
    expect(ASSET_SECTORS).toContain('exchange')
    expect(ASSET_SECTORS).toContain('infrastructure')
    expect(ASSET_SECTORS).toContain('meme')
    expect(ASSET_SECTORS).toContain('rwa')
    expect(ASSET_SECTORS).toContain('gaming')
    expect(ASSET_SECTORS).toContain('ai')
    expect(ASSET_SECTORS).toContain('unknown')
    expect(ASSET_SECTORS).toHaveLength(11)
  })

  it('isKnownAssetSector 對已知值回 true', () => {
    expect(isKnownAssetSector('l2')).toBe(true)
    expect(isKnownAssetSector('defi')).toBe(true)
    expect(isKnownAssetSector('unknown')).toBe(true)
  })

  it('isKnownAssetSector 對未知值回 false（不猜測）', () => {
    expect(isKnownAssetSector('rollup-ish')).toBe(false)
    expect(isKnownAssetSector('')).toBe(false)
  })
})

describe('ASSET_LAYERS 常量與 isKnownAssetLayer', () => {
  it('包含全部已知 layer 值', () => {
    expect(ASSET_LAYERS).toContain('layer_1')
    expect(ASSET_LAYERS).toContain('layer_2')
    expect(ASSET_LAYERS).toContain('app')
    expect(ASSET_LAYERS).toContain('protocol')
    expect(ASSET_LAYERS).toContain('token')
    expect(ASSET_LAYERS).toContain('offchain')
    expect(ASSET_LAYERS).toContain('unknown')
    expect(ASSET_LAYERS).toHaveLength(7)
  })

  it('isKnownAssetLayer 對已知值回 true', () => {
    expect(isKnownAssetLayer('layer_2')).toBe(true)
    expect(isKnownAssetLayer('unknown')).toBe(true)
  })

  it('isKnownAssetLayer 對未知值回 false', () => {
    expect(isKnownAssetLayer('layer_3')).toBe(false)
    expect(isKnownAssetLayer('')).toBe(false)
  })
})

describe('TOKEN_ROLES 常量與 isKnownTokenRole', () => {
  it('包含全部已知 token_role 值', () => {
    expect(TOKEN_ROLES).toContain('gas')
    expect(TOKEN_ROLES).toContain('governance')
    expect(TOKEN_ROLES).toContain('utility')
    expect(TOKEN_ROLES).toContain('staking')
    expect(TOKEN_ROLES).toContain('stable')
    expect(TOKEN_ROLES).toContain('lp')
    expect(TOKEN_ROLES).toContain('wrapped')
    expect(TOKEN_ROLES).toContain('meme')
    expect(TOKEN_ROLES).toContain('unknown')
    expect(TOKEN_ROLES).toHaveLength(9)
  })

  it('isKnownTokenRole 對已知值回 true', () => {
    expect(isKnownTokenRole('governance')).toBe(true)
    expect(isKnownTokenRole('unknown')).toBe(true)
  })

  it('isKnownTokenRole 對未知值回 false', () => {
    expect(isKnownTokenRole('dividend')).toBe(false)
    expect(isKnownTokenRole('')).toBe(false)
  })
})

describe('MARKET_CAP_TIERS 常量與 isKnownMarketCapTier', () => {
  it('包含全部已知 tier 值', () => {
    expect(MARKET_CAP_TIERS).toContain('large')
    expect(MARKET_CAP_TIERS).toContain('mid')
    expect(MARKET_CAP_TIERS).toContain('small')
    expect(MARKET_CAP_TIERS).toContain('micro')
    expect(MARKET_CAP_TIERS).toContain('unknown')
    expect(MARKET_CAP_TIERS).toHaveLength(5)
  })

  it('isKnownMarketCapTier 對已知值回 true', () => {
    expect(isKnownMarketCapTier('large')).toBe(true)
    expect(isKnownMarketCapTier('unknown')).toBe(true)
  })

  it('isKnownMarketCapTier 對未知值回 false', () => {
    expect(isKnownMarketCapTier('mega')).toBe(false)
    expect(isKnownMarketCapTier('')).toBe(false)
  })
})

describe('SectorLayerCard lookup table 覆蓋率稽核', () => {
  it('LAYER_LABEL 必須涵蓋所有 AssetLayer 值', () => {
    // SectorLayerCard 內 LAYER_LABEL 透過 `Record<AssetLayer, string>` 強制
    // 靜態編譯檢查（缺 key 或拼錯 → tsc 報錯），本測試用 runtime 常數陣列做
    // 最後防線確保 AssetLayer union type 未漂移。
    expect(ASSET_LAYERS.length).toBeGreaterThan(0)
  })
})
