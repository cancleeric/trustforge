// #20 主題切換 — `resolveInitialTheme` 純邏輯單元測試。規格見
// docs/PLAN-next-worldfirst-depth.md §2。

import { describe, expect, it } from 'vitest'
import { resolveInitialTheme, THEME_STORAGE_KEY, type ThemeEnv } from './theme'

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
