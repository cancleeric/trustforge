// CEO Round 2 終批修復（PR #103）：
//
// #1（HIGH｜legacy 快照炸掉 React overview）：驗證缺失／未知
// `decision_state` 一律正規化為 `'normal'`——`normalizeDecisionState()`
// 純函式 + `isOverviewData`/`isAnalyzeData`/`isHistoryData` 三個 validator
// 對「形狀合法但值未知」情境不再整包拒收（true 卻仍要求下游用
// `normalizeDecisionState()` 正規化才拿去判斷 hero/配色）。
//
// #4（LOW｜跨面板 invariant）：table-driven 驗證 normal/low_confidence/
// abstain 三態下 hero 選擇公式（`isLowInfo ? calibrated : raw`）與
// `bucketColor()` 配色公式在 React 側（`ConfidenceGauge`/`OverviewCard`
// 共用同一套邏輯）保持一致，並涵蓋 legacy/未知值 fallback。

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { normalizeDecisionState, type DecisionState } from './types'
import { isAnalyzeData, isHistoryData, isOverviewData } from './validators'
import { bucketColor } from './decisionColor'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function loadFixtureData<T>(name: string): T {
  const envelope = JSON.parse(readFileSync(path.join(__dirname, '__fixtures__', name), 'utf-8'))
  return envelope.data as T
}

describe('normalizeDecisionState — legacy 快照／未知 enum 值一律 fallback 為 normal', () => {
  it('已知三態原樣通過', () => {
    expect(normalizeDecisionState('abstain')).toBe('abstain')
    expect(normalizeDecisionState('low_confidence')).toBe('low_confidence')
    expect(normalizeDecisionState('normal')).toBe('normal')
  })

  it('缺失（undefined）正規化為 normal', () => {
    expect(normalizeDecisionState(undefined)).toBe('normal')
  })

  it('未知字面值（含 legacy 舊詞、未來新 enum）正規化為 normal', () => {
    expect(normalizeDecisionState('hold')).toBe('normal')
    expect(normalizeDecisionState('high_confidence')).toBe('normal')
    expect(normalizeDecisionState('')).toBe('normal')
    expect(normalizeDecisionState(null)).toBe('normal')
  })

  it('非字串型別（畸形值）也正規化為 normal，不 throw', () => {
    expect(normalizeDecisionState(123)).toBe('normal')
    expect(normalizeDecisionState({})).toBe('normal')
    expect(normalizeDecisionState([])).toBe('normal')
  })
})

describe('isOverviewData — decision_state 缺失/未知值不整包拒收（#1 修復）', () => {
  function baseCoin(extra: Record<string, unknown> = {}) {
    return {
      coin: 'BTC',
      trust_score: 0.59,
      direction: '中性',
      calibrated_confidence: 0.65,
      generated_at: '2026-07-03T21:40:04Z',
      fetched_at_epoch: 1783114801.6,
      ...extra,
    }
  }

  it('decision_state key 完全缺席（legacy 快照）仍視為合法', () => {
    const coin = baseCoin()
    expect('decision_state' in coin).toBe(false)
    expect(isOverviewData({ coins: [coin] })).toBe(true)
  })

  it('decision_state 為未知字面值仍視為合法（形狀層面放行，交由 normalizeDecisionState 兜底）', () => {
    expect(isOverviewData({ coins: [baseCoin({ decision_state: 'hold' })] })).toBe(true)
    expect(isOverviewData({ coins: [baseCoin({ decision_state: 'high_confidence' })] })).toBe(true)
  })

  it('decision_state 型別錯誤（非字串）仍判定畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ decision_state: 123 })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ decision_state: {} })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ decision_state: [] })] })).toBe(false)
  })
})

describe('isAnalyzeData — decision_state 缺失/未知值不整包拒收（真實 live 回應變形，#1 修復）', () => {
  it('live-analyze.json 拿掉 report.decision_state key 仍視為合法', () => {
    const data = loadFixtureData<{ report: Record<string, unknown> }>('live-analyze.json')
    delete data.report.decision_state
    expect(isAnalyzeData(data)).toBe(true)
  })

  it('live-analyze.json report.decision_state 換成未知字面值仍視為合法', () => {
    const data = loadFixtureData<{ report: Record<string, unknown> }>('live-analyze.json')
    data.report.decision_state = 'hold'
    expect(isAnalyzeData(data)).toBe(true)
  })
})

describe('isHistoryData — decision_state 缺失/未知值不整包拒收（#1 修復）', () => {
  function baseEntry(extra: Record<string, unknown> = {}) {
    return {
      date: '2026-07-01',
      coin: 'BTC',
      trust_score: 0.6,
      direction: '中性',
      calibrated_confidence: 0.5,
      generated_at: '2026-07-01T00:00:00Z',
      ...extra,
    }
  }

  it('decision_state key 完全缺席仍視為合法', () => {
    expect(isHistoryData({ coin: 'BTC', days: 7, history: [baseEntry()] })).toBe(true)
  })

  it('decision_state 為未知字面值仍視為合法', () => {
    expect(
      isHistoryData({ coin: 'BTC', days: 7, history: [baseEntry({ decision_state: 'hold' })] }),
    ).toBe(true)
  })

  it('decision_state 型別錯誤仍判定畸形，回傳 false', () => {
    expect(
      isHistoryData({ coin: 'BTC', days: 7, history: [baseEntry({ decision_state: 999 })] }),
    ).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// #2 修復：low_confidence 顏色語意——bucketColor 邊界值測試（React gauge）
// ---------------------------------------------------------------------------

describe('bucketColor（ConfidenceGauge） — state-aware 配色邊界值', () => {
  it('abstain 一律紅，不論數值高低', () => {
    expect(bucketColor('abstain', 0.0)).toBe('var(--color-tf-bad)')
    expect(bucketColor('abstain', 0.99)).toBe('var(--color-tf-bad)')
  })

  it('low_confidence 一律琥珀，不論數值高低（含 0.40 邊界值，同 CEO 舉例）', () => {
    expect(bucketColor('low_confidence', 0.0)).toBe('var(--color-tf-warn)')
    expect(bucketColor('low_confidence', 0.4)).toBe('var(--color-tf-warn)')
    expect(bucketColor('low_confidence', 0.99)).toBe('var(--color-tf-warn)')
  })

  it('normal 態按數值三分桶，邊界值精確', () => {
    expect(bucketColor('normal', 0.7)).toBe('var(--color-tf-good)')
    expect(bucketColor('normal', 0.69)).toBe('var(--color-tf-warn)')
    expect(bucketColor('normal', 0.45)).toBe('var(--color-tf-warn)')
    expect(bucketColor('normal', 0.44)).toBe('var(--color-tf-bad)')
    expect(bucketColor('normal', 0.4)).toBe('var(--color-tf-bad)') // 0.40：normal 態下是紅，跟 low_confidence 態的琥珀刻意不同
  })
})

// ---------------------------------------------------------------------------
// #4 修復：跨面板 invariant——hero 選擇 + 配色公式 table-driven（React 側）
// OverviewCard/ConfidenceGauge 共用同一套「isLowInfo ? calibrated : raw」
// 規則，這裡把該公式抽出來跟兩元件原始碼比對邏輯保持一致地單元測試。
// ---------------------------------------------------------------------------

function heroSelection(decisionState: DecisionState, calibrated: number, raw: number): number {
  const isLowInfo = decisionState === 'abstain' || decisionState === 'low_confidence'
  return isLowInfo ? calibrated : raw
}

describe('跨面板 invariant — hero 選擇 + 配色 parity（normal/low_confidence/abstain × legacy/未知 fallback）', () => {
  const cases: Array<{ raw: DecisionState | 'legacy_hold' | 'unknown_future'; calibrated: number; trust: number }> = [
    { raw: 'normal', calibrated: 0.3, trust: 0.8 },
    { raw: 'low_confidence', calibrated: 0.3, trust: 0.8 },
    { raw: 'abstain', calibrated: 0.3, trust: 0.8 },
    { raw: 'legacy_hold', calibrated: 0.3, trust: 0.8 },
    { raw: 'unknown_future', calibrated: 0.3, trust: 0.8 },
  ]

  it.each(cases)('$raw：hero 選擇與配色跟 normalizeDecisionState 後的三態一致', ({ raw, calibrated, trust }) => {
    const normalized = normalizeDecisionState(raw)
    const hero = heroSelection(normalized, calibrated, trust)
    const color = bucketColor(normalized, hero)

    if (raw === 'abstain') {
      expect(hero).toBe(calibrated)
      expect(color).toBe('var(--color-tf-bad)')
    } else if (raw === 'low_confidence') {
      expect(hero).toBe(calibrated)
      expect(color).toBe('var(--color-tf-warn)')
    } else {
      // normal、legacy_hold、unknown_future 皆正規化為 normal：主角＝裸均值信任分
      expect(normalized).toBe('normal')
      expect(hero).toBe(trust)
      expect(color).toBe('var(--color-tf-good)') // trust = 0.8 >= 0.7
    }
  })
})
