/**
 * Agent OS Badge component — visual classification badges for admin UI.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosBadgeVariant } from '../../lib/agosTypes'

const BADGE_CLASSES: Record<AgosBadgeVariant, string> = {
  historical: 'bg-gray-100 text-gray-700 border-gray-300',
  candidate: 'bg-yellow-50 text-yellow-800 border-yellow-300',
  trusted: 'bg-green-50 text-green-800 border-green-300',
  proposal: 'bg-white text-blue-700 border-blue-400 border-dashed',
  'risk-read': 'bg-green-50 text-green-700 border-green-200',
  'risk-local': 'bg-yellow-50 text-yellow-700 border-yellow-200',
  'risk-external': 'bg-orange-50 text-orange-700 border-orange-200',
  'risk-deploy': 'bg-red-50 text-red-700 border-red-200',
  'status-success': 'bg-green-50 text-green-700 border-green-200',
  'status-failed': 'bg-red-50 text-red-700 border-red-200',
  'status-pending': 'bg-blue-50 text-blue-700 border-blue-200',
  'status-timeout': 'bg-orange-50 text-orange-700 border-orange-200',
}

interface AgosBadgeProps {
  variant: AgosBadgeVariant
  label: string
}

export function AgosBadge({ variant, label }: AgosBadgeProps) {
  const classes = BADGE_CLASSES[variant] || BADGE_CLASSES.historical
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${classes}`}
      aria-label={label}
    >
      {label}
    </span>
  )
}

// ─── Helper functions for determining badge variant ─────────────────────────

export function evidenceBadgeVariant(eligible: boolean): AgosBadgeVariant {
  return eligible ? 'trusted' : 'historical'
}

export function statusBadgeVariant(
  status: string
): AgosBadgeVariant {
  switch (status) {
    case 'success':
      return 'status-success'
    case 'failed':
      return 'status-failed'
    case 'pending':
      return 'status-pending'
    case 'timeout':
      return 'status-timeout'
    default:
      return 'historical'
  }
}
