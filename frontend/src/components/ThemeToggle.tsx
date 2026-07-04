import { useEffect, useState } from 'react'
import {
  applyTheme,
  browserSystemThemeWatcher,
  browserThemeEnv,
  hasStoredTheme,
  persistTheme,
  resolveInitialTheme,
  type Theme,
} from '../lib/theme'

// #20 主題切換：純 client CSS（`<html data-theme>` + CSS 變數），不
// 觸發任何 API、不重新 fetch，已存在的分析結果 React state 不受影響。
// 初始值由 `index.html` 內嵌 script 先行套用（見 FOUC 防護），此處的
// `resolveInitialTheme` 只是讓 React state 跟 DOM 現況一致。
//
// codex 複審 MEDIUM：只從系統偏好推導出的初始值**不是使用者的明確選擇**，
// 不可在 mount 時就 persist 進 localStorage（見 `lib/theme.ts` 開頭註解）。
// 兩段語意分開處理：
//   - `hasFollowedSystem`：目前有沒有 `tf-theme`（使用者選過）。沒有的話
//     維持「跟隨系統」模式，訂閱 `prefers-color-scheme` 的 `change` 事件，
//     OS 主題變就跟著變，全程不寫 localStorage。
//   - 使用者按下 toggle 才是明確選擇：這裡才 `persistTheme()`，並停止跟隨
//     系統（解除訂閱），從此以使用者選擇為準。
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(browserThemeEnv()))
  const [userChosen, setUserChosen] = useState<boolean>(() => hasStoredTheme(browserThemeEnv()))

  // 只套用目前的主題到 DOM，不 persist——mount 時 theme 若來自系統推導，
  // 存下去會把「系統偏好」誤認成「使用者選擇」。
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // 使用者尚未明確選過主題時，持續跟隨系統 `prefers-color-scheme` 變化；
  // 使用者選過之後解除訂閱，OS 再變也不影響已選定的主題。
  useEffect(() => {
    if (userChosen) return
    const watcher = browserSystemThemeWatcher()
    return watcher.subscribe(setTheme)
  }, [userChosen])

  const isDark = theme === 'dark'
  const label = isDark ? '切換至淺色主題' : '切換至深色主題'

  function handleToggle() {
    const next: Theme = isDark ? 'light' : 'dark'
    setTheme(next)
    setUserChosen(true)
    persistTheme(next)
  }

  return (
    <button
      type="button"
      onClick={handleToggle}
      aria-label={label}
      title={label}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-tf-border text-sm leading-none text-tf-text2 transition hover:border-tf-link hover:text-tf-link"
    >
      <span aria-hidden="true">{isDark ? '☀️' : '🌙'}</span>
    </button>
  )
}
