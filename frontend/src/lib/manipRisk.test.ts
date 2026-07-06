// #86 codex 複審 HIGH/MEDIUM 修復——操縱風險徽章分級純邏輯測試：
// 門檻邊界、單筆確認操縱（worst-case 不被稀釋）、缺分數的顯式中性態。
//
// codex 複審 delta HIGH 修復——legacy payload 相容性測試：舊 writer 只寫
// manip_score（語意是平均值）、沒有 manip_score_mean 時，必須降級顯示
// unscored，不可套新門檻誤判成「低風險」。以下門檻/單筆確認操縱測試都
// 額外帶一個非 undefined 的 manipScoreMean，代表「新版 writer 已成對寫入」
// 這個前提，避免誤觸 legacy 分支。

import { describe, expect, it } from 'vitest'
import {
  MANIP_RISK_HIGH_THRESHOLD,
  MANIP_RISK_MEDIUM_THRESHOLD,
  MANIP_RISK_UNSCORED_LABEL,
  manipRiskDisplay,
} from './manipRisk'

describe('manipRiskDisplay — 門檻分級（新版 worst+mean 成對payload）', () => {
  it('低於中風險門檻 → 低風險（綠）', () => {
    expect(manipRiskDisplay(0, 0).tier).toBe('low')
    expect(manipRiskDisplay(MANIP_RISK_MEDIUM_THRESHOLD - 0.01, 0.01).tier).toBe('low')
  })

  it('恰為中風險門檻（含邊界）→ 中風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_MEDIUM_THRESHOLD, 0.05).tier).toBe('medium')
  })

  it('恰在中風險與高風險門檻之間 → 中風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_HIGH_THRESHOLD - 0.01, 0.1).tier).toBe('medium')
  })

  it('恰為高風險門檻（含邊界）→ 高風險', () => {
    expect(manipRiskDisplay(MANIP_RISK_HIGH_THRESHOLD, 0.2).tier).toBe('high')
  })

  it('高於高風險門檻 → 高風險', () => {
    expect(manipRiskDisplay(1.0, 0.067).tier).toBe('high')
  })
})

describe('manipRiskDisplay — 單筆確認操縱不被稀釋（HIGH invariant）', () => {
  it('manipScore=1.0（後端 worst-case，即使原始 evidence 有 14 筆乾淨、只 1 筆確認操縱）必須是高風險，不能因為上游用平均值而落到低/中風險', () => {
    // 這裡直接測 manipRiskDisplay 收到「已經是 worst-case 的分數」時的行為
    // ——上游稀釋防護鎖在 fetch_scheduler.py::_calc_manip_signal() 的
    // test_calc_manip_signal_single_confirmed_hit_is_not_diluted_by_mean，
    // 這裡鎖的是「收到 1.0 這個 worst-case 值、且 mean 也一併存在（代表新版
    // 成對 payload），分級結果一定是 high，不可能顯示低風險徽章」。
    const result = manipRiskDisplay(1.0, 0.067)
    expect(result.tier).toBe('high')
    expect(result.label).not.toContain('低操縱風險')
  })
})

describe('manipRiskDisplay — 缺分數顯式中性態（MEDIUM 修復）', () => {
  it('manipScore 與 manipScoreMean 皆 undefined（完全缺分數）時回傳明確的 unscored 中性態，不是拿低風險的樣式冒充', () => {
    const result = manipRiskDisplay(undefined, undefined)
    expect(result.tier).toBe('unscored')
    expect(result.label).toBe(MANIP_RISK_UNSCORED_LABEL)
  })

  it('unscored 顏色跟 low 顏色不同（避免「沒評分」與「已評估風險低」在視覺上混淆）', () => {
    const unscored = manipRiskDisplay(undefined, undefined)
    const low = manipRiskDisplay(0, 0)
    expect(unscored.color).not.toBe(low.color)
  })
})

describe('manipRiskDisplay — legacy payload 相容性（codex 複審 delta HIGH 修復）', () => {
  it('舊 mean-only payload（有 manip_score、無 manip_score_mean）→ 降級為 unscored，不可套新門檻誤判低風險', () => {
    // 模擬部署切換窗口／舊 writer 殘留快照：manip_score 實際上仍是舊語意的
    // 平均值（例：15 筆裡 1 筆確認操縱、平均稀釋成 0.067），若照新門檻
    // 判讀會誤判成低風險——這正是這輪要修的稀釋漏洞用「同名欄位換語意」
    // 復發的路徑，必須攔在這裡。
    const result = manipRiskDisplay(0.067, undefined)
    expect(result.tier).toBe('unscored')
    expect(result.label).toBe(MANIP_RISK_UNSCORED_LABEL)
    expect(result.legacy).toBe(true)
  })

  it('新版 worst+mean 成對 payload → 正常依 worst-case 分級，不受 legacy 分支影響', () => {
    const result = manipRiskDisplay(0.5, 0.1)
    expect(result.tier).toBe('high')
    expect(result.legacy).toBeFalsy()
  })

  it('完全缺分數（manipScore、manipScoreMean 皆 undefined）→ unscored，且不是 legacy（單純本輪無 evidence）', () => {
    const result = manipRiskDisplay(undefined, undefined)
    expect(result.tier).toBe('unscored')
    expect(result.legacy).toBeFalsy()
  })
})
