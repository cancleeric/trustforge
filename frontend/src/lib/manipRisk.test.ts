// #86 codex 複審 HIGH/MEDIUM 修復——操縱風險徽章分級純邏輯測試：
// 門檻邊界、單筆確認操縱（worst-case 不被稀釋）、缺分數的顯式中性態。

import { describe, expect, it } from 'vitest'
import {
  MANIP_RISK_HIGH_THRESHOLD,
  MANIP_RISK_MEDIUM_THRESHOLD,
  MANIP_RISK_UNSCORED_LABEL,
  manipRiskDisplay,
} from './manipRisk'

describe('manipRiskDisplay — 門檻分級', () => {
  it('低於中風險門檻 → 低風險（綠）', () => {
    expect(manipRiskDisplay(0).tier).toBe('low')
    expect(manipRiskDisplay(MANIP_RISK_MEDIUM_THRESHOLD - 0.01).tier).toBe('low')
  })

  it('恰為中風險門檻（含邊界）→ 中風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_MEDIUM_THRESHOLD).tier).toBe('medium')
  })

  it('恰在中風險與高風險門檻之間 → 中風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_HIGH_THRESHOLD - 0.01).tier).toBe('medium')
  })

  it('恰為高風險門檻（含邊界）→ 高風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_HIGH_THRESHOLD).tier).toBe('high')
  })

  it('高於高風險門檻 → 高風險', () => {
    expect(manipRiskDisplay(1.0).tier).toBe('high')
  })
})

describe('manipRiskDisplay — 單筆確認操縱不被稀釋（HIGH invariant）', () => {
  it('manipScore=1.0（後端 worst-case，即使原始 evidence 有 14 筆乾淨、只 1 筆確認操縱）必須是高風險，不能因為上游用平均值而落到低/中風險', () => {
    // 這裡直接測 manipRiskDisplay 收到「已經是 worst-case 的分數」時的行為
    // ——上游稀釋防護鎖在 fetch_scheduler.py::_calc_manip_signal() 的
    // test_calc_manip_signal_single_confirmed_hit_is_not_diluted_by_mean，
    // 這裡鎖的是「收到 1.0 這個 worst-case 值，分級結果一定是 high，不可能
    // 顯示低風險徽章」。
    const result = manipRiskDisplay(1.0)
    expect(result.tier).toBe('high')
    expect(result.label).not.toContain('低操縱風險')
  })
})

describe('manipRiskDisplay — 缺分數顯式中性態（MEDIUM 修復）', () => {
  it('undefined 時回傳明確的 unscored 中性態，不是拿低風險的樣式冒充', () => {
    const result = manipRiskDisplay(undefined)
    expect(result.tier).toBe('unscored')
    expect(result.label).toBe(MANIP_RISK_UNSCORED_LABEL)
  })

  it('unscored 顏色跟 low 顏色不同（避免「沒評分」與「已評估風險低」在視覺上混淆）', () => {
    const unscored = manipRiskDisplay(undefined)
    const low = manipRiskDisplay(0)
    expect(unscored.color).not.toBe(low.color)
  })
})
