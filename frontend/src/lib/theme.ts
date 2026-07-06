// #20 主題切換（light/dark）。純判斷邏輯（初始主題優先序）刻意跟
// DOM/瀏覽器 API 副作用分離成可注入的 `ThemeEnv`，維持專案現有測試慣例
// （vitest 環境是 node、不引入 jsdom），`resolveInitialTheme` 可直接用
// 假物件單元測試，不需要真的 window/document。
//
// 初始主題優先序：localStorage 有效值 > 系統偏好 prefers-color-scheme
// > dark（向後相容舊 SSR dark-only 的預設行為）。
// 規格：docs/plans/PLAN-next-worldfirst-depth.md §2。
//
// codex 複審 MEDIUM（初始偏好不當成明確選擇）：只從系統偏好推導出的主題
// **不算使用者選擇**，不可在 mount 就 persist 進 localStorage——否則使用者
// 從沒點過 toggle，`tf-theme` 卻已經存在，之後 OS 主題再變也不會跟著動。
// 正確語意：
//   1. 沒有 `tf-theme` 時＝「跟隨系統」模式，初始套用 `prefers-color-scheme`，
//      且持續訂閱其 `change` 事件（見 `browserSystemThemeWatcher`）。
//   2. 使用者按下 toggle 那一刻才是「明確選擇」，這時才 `persistTheme()`，
//      從此以 localStorage 為準、不再跟系統偏好走。
// `ThemeToggle.tsx` 負責串接這兩段：mount 只 `applyTheme`（不 persist），
// 只在 click handler 裡 `persistTheme`。

export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'tf-theme'

export interface ThemeEnv {
  getStoredTheme: () => string | null
  prefersLight: () => boolean
}

function isTheme(value: string | null): value is Theme {
  return value === 'light' || value === 'dark'
}

/** localStorage 是否已存在「使用者明確選過」的主題值（跟系統推導值互斥）。 */
export function hasStoredTheme(env: ThemeEnv): boolean {
  return isTheme(env.getStoredTheme())
}

/** `prefers-color-scheme` 的 matches 布林值 → `Theme` 的純轉換，供初始判斷
 *  跟系統偏好 change 事件 handler 共用同一套邏輯。 */
export function themeFromPrefersLight(prefersLight: boolean): Theme {
  return prefersLight ? 'light' : 'dark'
}

/** 初始主題判斷（純函式，不碰 DOM）。 */
export function resolveInitialTheme(env: ThemeEnv): Theme {
  const stored = env.getStoredTheme()
  if (isTheme(stored)) return stored
  return themeFromPrefersLight(env.prefersLight())
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

/** 純函式：把 `MediaQueryList` `change` 事件（或任何有 `.matches` 的假物件）
 *  轉成一次 `onChange(theme)` 呼叫。跟 `themeFromPrefersLight` 共用同一套
 *  「matches → Theme」邏輯，且不需要真的 `MediaQueryListEvent`，方便單元
 *  測試（傳 `{ matches: true/false }` 即可）。 */
export function makeSystemThemeChangeHandler(
  onChange: (theme: Theme) => void,
): (event: { matches: boolean }) => void {
  return (event) => onChange(themeFromPrefersLight(event.matches))
}

export interface SystemThemeWatcher {
  /** 訂閱系統 `prefers-color-scheme` 變化；回傳取消訂閱函式。
   *  呼叫端（`ThemeToggle.tsx`）只在「使用者尚未明確選過主題」時訂閱，
   *  一旦使用者按下 toggle 就該解除訂閱，避免明確選擇被系統偏好覆蓋。 */
  subscribe: (onChange: (theme: Theme) => void) => () => void
}

/** 真實瀏覽器環境的 `SystemThemeWatcher`。`matchMedia`/`addEventListener`
 *  皆可能拋錯或不存在（極舊瀏覽器），拋錯時回傳一個 no-op 取消函式，
 *  不讓「跟隨系統」這個加分功能弄炸整頁。 */
export function browserSystemThemeWatcher(): SystemThemeWatcher {
  return {
    subscribe: (onChange) => {
      try {
        const mql = window.matchMedia('(prefers-color-scheme: light)')
        const handler = makeSystemThemeChangeHandler(onChange)
        mql.addEventListener('change', handler)
        return () => mql.removeEventListener('change', handler)
      } catch {
        return () => {}
      }
    },
  }
}
