/**
 * ConflictBadge — 角度間衝突的視覺提示 pill (#810).
 */
import type { AngleConflict } from '../lib/multiAngleEndpoints'

interface ConflictBadgeProps {
  conflicts: AngleConflict[]
  currentAngle: string
}

const MODE_LABELS: Record<string, string> = {
  risk: '風險',
  sentiment: '情緒',
  fundamentals: '基本面',
  news: '新聞',
  catalyst: '催化',
}

export default function ConflictBadge({ conflicts, currentAngle }: ConflictBadgeProps) {
  const relevant = conflicts.filter(
    (c) => c.angle_a === currentAngle || c.angle_b === currentAngle,
  )
  if (relevant.length === 0) return null

  return (
    <span className="inline-flex gap-1 flex-wrap">
      {relevant.map((c, i) => {
        const other = c.angle_a === currentAngle ? c.angle_b : c.angle_a
        const label = MODE_LABELS[other] ?? other
        const icon = c.conflict_type === 'direction_divergence' ? '⚠️' : '📊'
        return (
          <span
            key={i}
            className="inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ backgroundColor: 'rgba(251, 146, 60, 0.2)', color: '#f97316' }}
            title={c.summary}
            role="status"
            aria-label={`與${label}${c.conflict_type === 'direction_divergence' ? '方向相反' : '完整度差距大'}`}
          >
            {icon} 與 {label}
          </span>
        )
      })}
    </span>
  )
}
