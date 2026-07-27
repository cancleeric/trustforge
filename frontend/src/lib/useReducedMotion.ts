/**
 * useReducedMotion — 低動態模式 hook（issue #231）。
 *
 * 1. 尊重系統 `prefers-reduced-motion: reduce` 設定
 * 2. 使用者可手動切換低動態模式
 * 3. 以 SameSite Cookie 保存偏好（不使用 localStorage）
 * 4. 低動態時在 <html> 加上 `data-reduced-motion` attribute，
 *    供 CSS 全域抑制動畫/轉場
 */
import { useCallback, useEffect, useState } from 'react'

const COOKIE_NAME = 'trustforge_hermes_reduced_motion'

function readCookie(): 'on' | 'off' | null {
  const match = document.cookie.split('; ').find((item) => item.startsWith(`${COOKIE_NAME}=`))
  if (!match) return null
  const val = match.split('=')[1]
  return val === 'on' ? 'on' : val === 'off' ? 'off' : null
}

function writeCookie(value: 'on' | 'off') {
  document.cookie = `${COOKIE_NAME}=${value}; Max-Age=31536000; Path=/; SameSite=Lax`
}

function getSystemPreference(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function resolveInitialState(): boolean {
  const cookie = readCookie()
  if (cookie === 'on') return true
  if (cookie === 'off') return false
  // No explicit user choice — follow system setting
  return getSystemPreference()
}

/**
 * N63：把偏好套到 <html> 上，一次性、與 React 無關。
 *
 * 為什麼需要這支：useReducedMotion 只掛在 HermesDashboard（路由 `/`）裡，
 * 所以「直接開 /analyze、/settings、/help…」的人，cookie 明明存著 on，
 * 卻沒有任何程式碼去讀它——data-reduced-motion 從頭到尾沒被加上，全站動畫照跑。
 * 實測 8 條路由裡有 7 條 attr=false，還有 27 類動畫在跑。低動態是「使用者偏好」，
 * 不是「首頁功能」，它必須在 app 啟動時就生效，跟哪個畫面被 render 無關。
 *
 * 放在啟動點而不是 App 元件裡，是為了不製造第二份 useState——真正的開關狀態
 * 仍然只有 HermesDashboard 那一份，這裡只負責開場把 DOM 校準到既有偏好。
 */
export function applyReducedMotionAttribute() {
  if (typeof document === 'undefined') return
  if (resolveInitialState()) {
    document.documentElement.setAttribute('data-reduced-motion', '')
  } else {
    document.documentElement.removeAttribute('data-reduced-motion')
  }
}

export function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState<boolean>(resolveInitialState)
  const [userChosen, setUserChosen] = useState<boolean>(() => readCookie() !== null)

  // Sync DOM attribute
  useEffect(() => {
    if (reducedMotion) {
      document.documentElement.setAttribute('data-reduced-motion', '')
    } else {
      document.documentElement.removeAttribute('data-reduced-motion')
    }
  }, [reducedMotion])

  // Follow system preference changes when user hasn't explicitly chosen
  useEffect(() => {
    if (userChosen) return
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [userChosen])

  const toggle = useCallback(() => {
    setReducedMotion((prev) => {
      const next = !prev
      writeCookie(next ? 'on' : 'off')
      setUserChosen(true)
      return next
    })
  }, [])

  return { reducedMotion, toggle } as const
}
