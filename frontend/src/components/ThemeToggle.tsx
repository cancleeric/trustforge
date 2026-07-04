import { useEffect, useState } from 'react'
import { applyTheme, browserThemeEnv, persistTheme, resolveInitialTheme, type Theme } from '../lib/theme'

// #20 主題切換：純 client CSS（`<html data-theme>` + CSS 變數），不
// 觸發任何 API、不重新 fetch，已存在的分析結果 React state 不受影響。
// 初始值由 `index.html` 內嵌 script 先行套用（見 FOUC 防護），此處的
// `resolveInitialTheme` 只是讓 React state 跟 DOM 現況一致。
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(browserThemeEnv()))

  useEffect(() => {
    applyTheme(theme)
    persistTheme(theme)
  }, [theme])

  const isDark = theme === 'dark'
  const label = isDark ? '切換至淺色主題' : '切換至深色主題'

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      aria-label={label}
      title={label}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-tf-border text-sm leading-none text-tf-text2 transition hover:border-tf-link hover:text-tf-link"
    >
      <span aria-hidden="true">{isDark ? '☀️' : '🌙'}</span>
    </button>
  )
}
