// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import type { EvidenceGroup } from './types'
import {
  buildGroupMap,
  getGroupForIdx,
  getRenderGroups,
  isGrouped,
  trendLabel,
} from './evidenceGrouping'

const group1: EvidenceGroup = {
  representative_idx: 2,
  member_indices: [0, 1, 2],
  trend: 'rising',
  value_range: '828–891 TH/s',
  latest_value: '891.0 TH/s',
}

const group2: EvidenceGroup = {
  representative_idx: 3,
  member_indices: [3],
  trend: null,
  value_range: null,
  latest_value: null,
}

describe('buildGroupMap', () => {
  it('returns null for undefined input', () => {
    expect(buildGroupMap(undefined)).toBeNull()
  })

  it('returns null for null input', () => {
    expect(buildGroupMap(null)).toBeNull()
  })

  it('returns null for empty array', () => {
    expect(buildGroupMap([])).toBeNull()
  })

  it('builds correct map from groups', () => {
    const map = buildGroupMap([group1, group2])!
    expect(map).not.toBeNull()
    expect(map.get(0)).toBe(group1)
    expect(map.get(1)).toBe(group1)
    expect(map.get(2)).toBe(group1)
    expect(map.get(3)).toBe(group2)
    expect(map.get(99)).toBeUndefined()
  })
})

describe('isGrouped', () => {
  it('returns false when groupMap is null', () => {
    expect(isGrouped(0, null)).toBe(false)
  })

  it('returns true for member of multi-member group', () => {
    const map = buildGroupMap([group1, group2])!
    expect(isGrouped(0, map)).toBe(true)
    expect(isGrouped(1, map)).toBe(true)
    expect(isGrouped(2, map)).toBe(true)
  })

  it('returns false for singleton group member', () => {
    const map = buildGroupMap([group1, group2])!
    expect(isGrouped(3, map)).toBe(false)
  })

  it('returns false for unknown index', () => {
    const map = buildGroupMap([group1])!
    expect(isGrouped(99, map)).toBe(false)
  })
})

describe('getGroupForIdx', () => {
  it('returns null when groupMap is null', () => {
    expect(getGroupForIdx(0, null)).toBeNull()
  })

  it('returns correct group for known index', () => {
    const map = buildGroupMap([group1, group2])!
    expect(getGroupForIdx(1, map)).toBe(group1)
    expect(getGroupForIdx(3, map)).toBe(group2)
  })

  it('returns null for unknown index', () => {
    const map = buildGroupMap([group1])!
    expect(getGroupForIdx(99, map)).toBeNull()
  })
})

describe('getRenderGroups', () => {
  it('returns empty array for null/undefined', () => {
    expect(getRenderGroups(null)).toEqual([])
    expect(getRenderGroups(undefined)).toEqual([])
  })

  it('returns empty array for empty groups', () => {
    expect(getRenderGroups([])).toEqual([])
  })

  it('returns groups as-is', () => {
    const groups = [group1, group2]
    expect(getRenderGroups(groups)).toBe(groups)
  })
})

describe('trendLabel', () => {
  it('returns Chinese labels by default', () => {
    expect(trendLabel('rising')).toBe('上升趨勢')
    expect(trendLabel('falling')).toBe('下降趨勢')
    expect(trendLabel('stable')).toBe('持平')
  })

  it('returns English labels', () => {
    expect(trendLabel('rising', 'en')).toBe('Rising')
    expect(trendLabel('falling', 'en')).toBe('Falling')
    expect(trendLabel('stable', 'en')).toBe('Stable')
  })

  it('returns empty string for null', () => {
    expect(trendLabel(null)).toBe('')
  })
})
