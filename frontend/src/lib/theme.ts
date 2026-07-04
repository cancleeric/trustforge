// #20 主題切換（light/dark）。純判斷邏輯（初始主題優先序）刻意跟
// DOM/瀏覽器 API 副作用分離成可注入的 `ThemeEnv`，維持專案現有測試慣例
// （vitest 環境是 node、不引入 jsdom），`resolveInitialTheme` 可直接用
// 假物件單元測試，不需要真的 window/document。
//
// 初始主題優先序：localStorage 有效值 > 系統偏好 prefers-color-scheme
// > dark（向後相容舊 SSR dark-only 的預設行為）。
// 規格：docs/PLAN-next-worldfirst-depth.md §2。

export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'tf-theme'

export interface ThemeEnv {
  getStoredTheme: () => string | null
  prefersLight: () => boolean
}

function isTheme(value: string | null): value is Theme {
  return value === 'light' || value === 'dark'
}

/** 初始主題判斷（純函式，不碰 DOM）。 */
export function resolveInitialTheme(env: ThemeEnv): Theme {
  const stored = env.getStoredTheme()
  if (isTheme(stored)) return stored
  return env.prefersLight() ? 'light' : 'dark'
}

/** 真實瀏覽器環境的 ThemeEnv：讀 localStorage / matchMedia。
 *  兩者都可能拋錯（無痕模式擋 localStorage、極舊瀏覽器沒有
 *  matchMedia），一律靜默退回，不讓主題判斷炸掉整頁。 */
export function browserThemeEnv(): ThemeEnv {
  return {
    getStoredTheme: () => {
      try {
        return window.localStorage.getItem(THEME_STORAGE_KEY)
      } catch {
        return null
      }
    },
    prefersLight: () => {
      try {
        return window.matchMedia('(prefers-color-scheme: light)').matches
      } catch {
        return false
      }
    },
  }
}

/** 套用主題：只改 `<html data-theme>` 屬性 + CSS 變數，不觸發任何 API。 */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
}

export function persistTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // localStorage 不可用時靜默略過，不影響本次切換的視覺效果。
  }
}
