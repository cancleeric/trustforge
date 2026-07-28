/**
 * ConflictBadge — 角度間衝突的視覺提示 pill (#810).
 */
import type { AngleConflict } from '../lib/multiAngleEndpoints'
import { useHermesI18n } from './hermesI18n'

interface ConflictBadgeProps {
  conflicts: AngleConflict[]
  currentAngle: string
}

const SHORT_KEY_MAP: Record<string, string> = {
  risk: 'maRiskShort',
  sentiment: 'maSentimentShort',
  fundamentals: 'maFundamentalsShort',
  news: 'maNewsShort',
  catalyst: 'maCatalystShort',
}

export default function ConflictBadge({ conflicts, currentAngle }: ConflictBadgeProps) {
  const { t } = useHermesI18n()
  const relevant = conflicts.filter(
    (c) => c.angle_a === currentAngle || c.angle_b === currentAngle,
  )
  if (relevant.length === 0) return null

  return (
    <span className="inline-flex gap-1 flex-wrap">
      {relevant.map((c, i) => {
        const other = c.angle_a === currentAngle ? c.angle_b : c.angle_a
        const label = t(SHORT_KEY_MAP[other]) ?? other
        const icon = c.conflict_type === 'direction_divergence' ? '⚠️' : '📊'
        const reason = c.conflict_type === 'direction_divergence'
          ? t('maConflictDirOpposite')
          : t('maConflictGapLarge')
        return (
          <span
            key={i}
            className="inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ backgroundColor: 'rgba(251, 146, 60, 0.2)', color: '#f97316' }}
            title={c.summary}
            role="status"
            aria-label={t('maConflictAriaLabel', { other: label, reason })}
          >
            {icon} {t('maConflictWithPrefix', { other: label })}
          </span>
        )
      })}
    </span>
  )
}
