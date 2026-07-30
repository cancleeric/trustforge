/**
 * FpsMeter — 即時 FPS 顯示器 + 品質等級指示
 *
 * 位置與窄螢幕精簡顯示由 hermes.css 的穩定 layout contract 控制。
 * 顏色隨 FPS 變化：綠 >= 50, 黃 30-49, 紅 < 30
 */
import type { QualityLevel } from './useAdaptiveQuality'

interface FpsMeterProps {
  fps: number
  quality: QualityLevel
  measuring: boolean
  labels?: Record<QualityLevel | 'detecting', string>
}

const DEFAULT_LABELS: Record<QualityLevel | 'detecting', string> = {
  high: 'HIGH',
  medium: 'MED',
  low: 'LOW',
  detecting: 'DETECTING…',
}

export default function FpsMeter({ fps, quality, measuring, labels = DEFAULT_LABELS }: FpsMeterProps) {
  const color = fps >= 50 ? '#4dd8e0' : fps >= 30 ? '#e8b34d' : '#ff5f5f'
  const qualityColor = quality === 'high' ? '#4dd8e0' : quality === 'medium' ? '#e8b34d' : '#ff5f5f'
  const qualityLabel = measuring ? labels.detecting : labels[quality]

  return (
    <div
      className="hermes-fps-meter"
      role="img"
      aria-label={`${fps} FPS · ${qualityLabel}`}
    >
      {/* FPS number */}
      <span className="hermes-fps-value" style={{ color }}>
        {fps}
      </span>
      <span className="hermes-fps-unit">FPS</span>

      {/* Separator */}
      <span className="hermes-fps-separator" aria-hidden="true" />

      {/* Quality badge */}
      <span
        className="hermes-fps-quality"
        data-short={measuring ? '…' : labels[quality].slice(0, 1)}
        style={{ color: qualityColor }}
      >
        {qualityLabel}
      </span>

      {/* Status dot */}
      <span
        className="hermes-fps-dot"
        style={{ background: color, boxShadow: `0 0 6px ${color}` }}
        aria-hidden="true"
      />
    </div>
  )
}
