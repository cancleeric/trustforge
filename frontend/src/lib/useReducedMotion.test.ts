// @vitest-environment jsdom
/**
 * N63：低動態偏好必須「全站」生效，不能只在首頁。
 *
 * RED 的來源是實測而不是推測：cookie 存 on 的情況下直接開 8 條路由，
 * 只有 `/` 的 <html> 帶得到 data-reduced-motion，其餘 7 條 attr=false，
 * 而且用 Web Animations API 數得出 27 類動畫仍在 running。原因是
 * useReducedMotion 這支 hook 只掛在 HermesDashboard 裡，別的路由根本沒人去讀
 * 那個 cookie。低動態是使用者偏好，不是首頁功能。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyReducedMotionAttribute } from './useReducedMotion'

const COOKIE = 'trustforge_hermes_reduced_motion'

function setCookie(value: string | null) {
  if (value === null) {
    document.cookie = `${COOKIE}=; Max-Age=0; Path=/`
    return
  }
  document.cookie = `${COOKIE}=${value}; Path=/`
}

function mockSystemPreference(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: reduce && q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
}

describe('applyReducedMotionAttribute — 偏好在 app 啟動時就套用', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-reduced-motion')
    setCookie(null)
    mockSystemPreference(false)
  })

  it('cookie=on：不管 render 哪個畫面，<html> 都要帶 data-reduced-motion', () => {
    setCookie('on')
    applyReducedMotionAttribute()
    expect(document.documentElement.hasAttribute('data-reduced-motion')).toBe(true)
  })

  it('cookie=off：使用者明確關掉，就算系統偏好要求也不套用', () => {
    setCookie('off')
    mockSystemPreference(true)
    applyReducedMotionAttribute()
    expect(document.documentElement.hasAttribute('data-reduced-motion')).toBe(false)
  })

  it('沒有 cookie：跟隨系統 prefers-reduced-motion', () => {
    mockSystemPreference(true)
    applyReducedMotionAttribute()
    expect(document.documentElement.hasAttribute('data-reduced-motion')).toBe(true)
  })

  it('沒有 cookie 且系統沒要求：維持一般動態', () => {
    applyReducedMotionAttribute()
    expect(document.documentElement.hasAttribute('data-reduced-motion')).toBe(false)
  })

  it('偏好變成 off 時要把既有 attribute 清掉，而不是只會加不會減', () => {
    document.documentElement.setAttribute('data-reduced-motion', '')
    setCookie('off')
    applyReducedMotionAttribute()
    expect(document.documentElement.hasAttribute('data-reduced-motion')).toBe(false)
  })
})
