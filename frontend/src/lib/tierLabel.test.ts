import { describe, expect, it } from 'vitest'
import { tierLabel, TONE_COLOR } from './tierLabel'

describe('tierLabel', () => {
  // ── 三態結構化狀態（優先於純數字分桶）────────────────────────────
  it('abstain 回「棄權／資料不足」且 tone=bad，不看 heroValue', () => {
    expect(tierLabel('abstain', 0.99)).toEqual({ label: '棄權／資料不足', tone: 'bad' })
    expect(tierLabel('abstain', 0.0)).toEqual({ label: '棄權／資料不足', tone: 'bad' })
  })

  it('low_confidence 回「資訊完整度偏低」且 tone=warn', () => {
    expect(tierLabel('low_confidence', 0.99)).toEqual({ label: '資訊完整度偏低', tone: 'warn' })
    expect(tierLabel('low_confidence', 0.0)).toEqual({ label: '資訊完整度偏低', tone: 'warn' })
  })

  // ── normal 態：吃 heroValue，門檻對齊 schema.py（0.7 / 0.45）─────
  it('normal hero=0.7 邊界回「高」tone=good', () => {
    expect(tierLabel('normal', 0.7)).toEqual({ label: '高', tone: 'good' })
  })

  it('normal hero=0.6999（略低於 0.7）落入「中」tone=warn，不誤標高', () => {
    expect(tierLabel('normal', 0.6999)).toEqual({ label: '中', tone: 'warn' })
  })

  it('normal hero=0.45 邊界回「中」tone=warn', () => {
    expect(tierLabel('normal', 0.45)).toEqual({ label: '中', tone: 'warn' })
  })

  it('normal hero=0.4499（略低於 0.45）落入「低」tone=bad', () => {
    expect(tierLabel('normal', 0.4499)).toEqual({ label: '低', tone: 'bad' })
  })

  it('normal hero=0 回「低」tone=bad', () => {
    expect(tierLabel('normal', 0)).toEqual({ label: '低', tone: 'bad' })
  })

  it('normal 高值 hero=1 回「高」tone=good', () => {
    expect(tierLabel('normal', 1)).toEqual({ label: '高', tone: 'good' })
  })

  // ── legacy / 未知 enum：經 normalize 落 normal，依 heroValue 分桶 ──
  it('未知/legacy decisionState 經 normalize 落 normal，依 heroValue 分桶', () => {
    expect(tierLabel('unknown_legacy_value', 0.8)).toEqual({ label: '高', tone: 'good' })
    expect(tierLabel('some_future_enum', 0.5)).toEqual({ label: '中', tone: 'warn' })
    expect(tierLabel('', 0.1)).toEqual({ label: '低', tone: 'bad' })
  })

  // ── tone → 顏色 token 映射對齊 decisionColor / Badges 既有用法 ──
  it('TONE_COLOR 映射 good/warn/bad 到既有 css var token', () => {
    expect(TONE_COLOR.good).toBe('var(--color-tf-good)')
    expect(TONE_COLOR.warn).toBe('var(--color-tf-warn)')
    expect(TONE_COLOR.bad).toBe('var(--color-tf-bad)')
  })
})
