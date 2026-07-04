// #20 主題切換 — `resolveInitialTheme` 純邏輯單元測試。規格見
// docs/PLAN-next-worldfirst-depth.md §2。
//
// codex 複審 MEDIUM（初始偏好不當成明確選擇）：新增 `hasStoredTheme`／
// `themeFromPrefersLight`／`makeSystemThemeChangeHandler` 的測試，涵蓋
// 「沒存過主題時應持續跟隨系統偏好」跟「使用者選過後才算數」這兩段語意。

import { describe, expect, it, vi } from 'vitest'
import {
  hasStoredTheme,
  makeSystemThemeChangeHandler,
  resolveInitialTheme,
  themeFromPrefersLight,
  THEME_STORAGE_KEY,
  type ThemeEnv,
} from './theme'

function env(stored: string | null, prefersLight: boolean): ThemeEnv {
  return {
    getStoredTheme: () => stored,
    prefersLight: () => prefersLight,
  }
}

describe('resolveInitialTheme', () => {
  it('localStorage 有合法值時優先採用，不管系統偏好為何', () => {
    expect(resolveInitialTheme(env('light', false))).toBe('light')
    expect(resolveInitialTheme(env('dark', true))).toBe('dark')
  })

  it('localStorage 無值時，退回系統偏好 prefers-color-scheme', () => {
    expect(resolveInitialTheme(env(null, true))).toBe('light')
    expect(resolveInitialTheme(env(null, false))).toBe('dark')
  })

  it('localStorage 值不合法（非 light/dark）時視同無值，退回系統偏好', () => {
    expect(resolveInitialTheme(env('sepia', true))).toBe('light')
    expect(resolveInitialTheme(env('', false))).toBe('dark')
  })

  it('localStorage 無值且系統偏好也讀不到時，預設 dark（向後相容舊行為）', () => {
    expect(resolveInitialTheme(env(null, false))).toBe('dark')
  })

  it('storage key 常數維持穩定，避免不小心改動造成舊使用者選擇失效', () => {
    expect(THEME_STORAGE_KEY).toBe('tf-theme')
  })
})

describe('hasStoredTheme', () => {
  it('localStorage 有合法值（light/dark）時視為使用者已明確選過', () => {
    expect(hasStoredTheme(env('light', false))).toBe(true)
    expect(hasStoredTheme(env('dark', true))).toBe(true)
  })

  it('沒有值或值不合法時，視為使用者從沒選過（維持跟隨系統模式）', () => {
    expect(hasStoredTheme(env(null, true))).toBe(false)
    expect(hasStoredTheme(env('sepia', false))).toBe(false)
    expect(hasStoredTheme(env('', true))).toBe(false)
  })
})

describe('themeFromPrefersLight', () => {
  it('matches=true 對應 light，matches=false 對應 dark', () => {
    expect(themeFromPrefersLight(true)).toBe('light')
    expect(themeFromPrefersLight(false)).toBe('dark')
  })
})

describe('makeSystemThemeChangeHandler', () => {
  it('系統 prefers-color-scheme 變化時，把 matches 轉成 Theme 呼叫 onChange', () => {
    const onChange = vi.fn()
    const handler = makeSystemThemeChangeHandler(onChange)

    handler({ matches: true })
    expect(onChange).toHaveBeenLastCalledWith('light')

    handler({ matches: false })
    expect(onChange).toHaveBeenLastCalledWith('dark')

    expect(onChange).toHaveBeenCalledTimes(2)
  })
})
