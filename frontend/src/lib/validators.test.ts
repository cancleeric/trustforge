// #86 跨幣信任×操縱風險排行——`isOverviewData`/`isOverviewCoin` 對
// `manip_score` 選填欄位的驗證邏輯純單元測試。同 `reputation_trace` 慣例：
// 後端 `_snapshot_dict()`（scripts/fetch_scheduler.py）在該幣本輪無
// evidence／舊格式快照時完全不會帶這個 key，不是漏資料——過嚴驗證（要求
// 必為 number）會把這種合法情況當成畸形而 parse_error，誤殺整張總覽卡。

import { describe, expect, it } from 'vitest'
import { isOverviewData } from './validators'

function baseCoin(extra: Record<string, unknown> = {}) {
  return {
    coin: 'BTC',
    trust_score: 0.59,
    direction: '中性',
    calibrated_confidence: 0.65,
    decision_state: 'normal',
    generated_at: '2026-07-03T21:40:04Z',
    fetched_at_epoch: 1783114801.6,
    ...extra,
  }
}

describe('isOverviewData — manip_score 選填欄位（#86）', () => {
  it('key 不存在時仍視為合法（舊格式快照／本輪無 evidence）', () => {
    expect(isOverviewData({ coins: [baseCoin()] })).toBe(true)
  })

  it('key 存在且為 number 時通過', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 0.42 })] })).toBe(true)
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 0 })] })).toBe(true)
  })

  it('key 存在但型別錯誤時判定為畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: '0.42' })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ manip_score: null })] })).toBe(false)
  })
})

describe('isOverviewData — manip_score_mean 選填欄位（#86 codex 複審 HIGH 修復追加）', () => {
  it('key 不存在時仍視為合法（同 manip_score 缺席規則）', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 1.0 })] })).toBe(true)
  })

  it('manip_score 為 worst-case、manip_score_mean 為輔助平均，兩者可同時存在且不要求相等', () => {
    // 真實情境：15 筆 evidence 裡 1 筆已確認操縱，worst=1.0，mean 遠低於 1.0——
    // 驗證 validator 不會誤把兩者混為一談或要求數值一致。
    expect(
      isOverviewData({ coins: [baseCoin({ manip_score: 1.0, manip_score_mean: 0.067 })] }),
    ).toBe(true)
  })

  it('key 存在但型別錯誤時判定為畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score_mean: '0.2' })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ manip_score_mean: null })] })).toBe(false)
  })
})

describe('isOverviewData — manip_score／manip_score_mean 越界值拒絕（codex 窮舉終審 MEDIUM 修復）', () => {
  // 原本只驗 `typeof === 'number'`，`NaN`/`Infinity`/負值/大於 1 的值全部
  // 是 JS 的 `typeof number`，會通過檢查、一路餵進 `manipRiskDisplay()` 的
  // 門檻比較——負值必然小於任何正門檻，會被誤判成「低操縱風險」，把畸形
  // 資料偽裝成一個確定的安全結論。改用 `Number.isFinite` + 0..1 範圍限制
  // 在 validator 層就擋掉。
  it('NaN 判定為畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: NaN })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ manip_score_mean: NaN })] })).toBe(false)
  })

  it('Infinity／-Infinity 判定為畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: Infinity })] })).toBe(false)
    expect(isOverviewData({ coins: [baseCoin({ manip_score: -Infinity })] })).toBe(false)
  })

  it('負值判定為畸形，回傳 false（不可放行成假低風險）', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: -0.1 })] })).toBe(false)
  })

  it('大於 1 的值判定為畸形，回傳 false', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 1.1 })] })).toBe(false)
  })

  it('邊界值 0 與 1 皆合法通過', () => {
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 0 })] })).toBe(true)
    expect(isOverviewData({ coins: [baseCoin({ manip_score: 1 })] })).toBe(true)
  })
})
