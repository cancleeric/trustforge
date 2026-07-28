/**
 * FpsMeter — 即時 FPS 顯示器 + 品質等級指示
 *
 * 顯示在畫面右下角（不遮擋主要 UI），HERMES 風格。
 * 顏色隨 FPS 變化：綠 >= 50, 黃 30-49, 紅 < 30
 */
import type { QualityLevel } from './useAdaptiveQuality'

interface FpsMeterProps {
  fps: number
  quality: QualityLevel
  measuring: boolean
}

const QUALITY_LABELS: Record<QualityLevel, string> = {
  high: 'HIGH',
  medium: 'MED',
  low: 'LOW',
}

export default function FpsMeter({ fps, quality, measuring }: FpsMeterProps) {
  const color = fps >= 50 ? '#4dd8e0' : fps >= 30 ? '#e8b34d' : '#ff5f5f'
  const qualityColor = quality === 'high' ? '#4dd8e0' : quality === 'medium' ? '#e8b34d' : '#ff5f5f'

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 8,
        left: 8,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 10px',
        background: 'rgba(2,4,10,.85)',
        border: `1px solid ${color}44`,
        borderRadius: 4,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 10,
        letterSpacing: '0.5px',
        pointerEvents: 'none',
        userSelect: 'none',
      }}
    >
      {/* FPS number */}
      <span style={{ color, fontWeight: 700, fontSize: 13, minWidth: 28, textAlign: 'right' }}>
        {fps}
      </span>
      <span style={{ color: 'rgba(255,255,255,.5)' }}>FPS</span>

      {/* Separator */}
      <span style={{ width: 1, height: 14, background: 'rgba(255,255,255,.15)' }} />

      {/* Quality badge */}
      <span style={{ color: qualityColor, fontWeight: 600 }}>
        {measuring ? 'DETECTING…' : QUALITY_LABELS[quality]}
      </span>

      {/* Status dot */}
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 6px ${color}`,
        }}
      />
    </div>
  )
}
