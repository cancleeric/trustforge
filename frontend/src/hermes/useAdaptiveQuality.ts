/**
 * useAdaptiveQuality — 自適應動畫品質系統 + 即時 FPS 計數器
 *
 * 啟動後量測 2 秒 FPS，根據結果自動選擇品質等級：
 *   high:   所有動畫全開（>= 45fps）
 *   medium: 關閉星空視差 + 軌道旋轉（30-44fps）
 *   low:    等同 reduced-motion，只保留文字/數據（< 30fps）
 *
 * 同時持續量測並輸出即時 FPS 供顯示用。
 * 偏好存入 localStorage，下次載入直接套用。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export type QualityLevel = 'high' | 'medium' | 'low'

const STORAGE_KEY = 'trustforge_adaptive_quality'
const MEASURE_DURATION_MS = 2000
const CONTINUOUS_SAMPLE_SIZE = 30 // 滾動平均用最近 N 幀

function readStoredQuality(): QualityLevel | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'high' || v === 'medium' || v === 'low') return v
  } catch { /* localStorage unavailable */ }
  return null
}

function writeStoredQuality(level: QualityLevel) {
  try { localStorage.setItem(STORAGE_KEY, level) } catch { /* noop */ }
}

function fpsToQuality(fps: number): QualityLevel {
  if (fps >= 45) return 'high'
  if (fps >= 30) return 'medium'
  return 'low'
}

export function useAdaptiveQuality() {
  const [quality, setQuality] = useState<QualityLevel>(() => readStoredQuality() || 'high')
  const [fps, setFps] = useState(60)
  const [measuring, setMeasuring] = useState(true)
  const rafRef = useRef(0)
  const frameTimesRef = useRef<number[]>([])

  // Initial measurement: determine quality over 2s
  useEffect(() => {
    const stored = readStoredQuality()
    if (stored) {
      setQuality(stored)
      setMeasuring(false)
      // Still start continuous FPS tracking
    }

    let frames = 0
    let lastTime = performance.now()
    const startTime = lastTime
    const recentFrames: number[] = []

    function tick(now: number) {
      const delta = now - lastTime
      lastTime = now
      frames++
      recentFrames.push(delta)

      // Keep rolling window for continuous FPS display
      if (recentFrames.length > CONTINUOUS_SAMPLE_SIZE) {
        recentFrames.shift()
      }

      // Update displayed FPS every ~500ms (30 frames)
      if (frames % 15 === 0) {
        const avg = recentFrames.reduce((a, b) => a + b, 0) / recentFrames.length
        setFps(Math.round(1000 / avg))
      }

      // After measurement period, determine quality
      const elapsed = now - startTime
      if (elapsed >= MEASURE_DURATION_MS && measuring) {
        const avgFps = (frames / elapsed) * 1000
        const detectedQuality = fpsToQuality(avgFps)
        if (!stored) {
          setQuality(detectedQuality)
          writeStoredQuality(detectedQuality)
        }
        setMeasuring(false)
      }

      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
    frameTimesRef.current = recentFrames

    return () => cancelAnimationFrame(rafRef.current)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Apply quality level to DOM as a data attribute
  useEffect(() => {
    document.documentElement.setAttribute('data-quality', quality)
  }, [quality])

  const setManualQuality = useCallback((level: QualityLevel) => {
    setQuality(level)
    writeStoredQuality(level)
  }, [])

  const resetAutoDetect = useCallback(() => {
    try { localStorage.removeItem(STORAGE_KEY) } catch { /* noop */ }
    setMeasuring(true)
    setQuality('high') // Start high, let measurement determine
  }, [])

  return { quality, fps, measuring, setQuality: setManualQuality, resetAutoDetect } as const
}
