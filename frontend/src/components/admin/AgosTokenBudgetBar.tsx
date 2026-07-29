/**
 * Token budget progress bar for Agent OS context manifests.
 *
 * Issue: #924 | Epic: #914
 */

interface AgosTokenBudgetBarProps {
  used: number
  total: number
}

export function AgosTokenBudgetBar({ used, total }: AgosTokenBudgetBarProps) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0
  const color = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-green-500'

  return (
    <div className="w-full">
      <div
        className="w-full bg-gray-200 rounded h-4 overflow-hidden"
        role="progressbar"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={`Token budget: ${used} of ${total} used`}
      >
        <div
          className={`${color} h-4 rounded transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-gray-600 mt-1">
        {used.toLocaleString()} / {total.toLocaleString()} tokens ({pct.toFixed(0)}%)
      </p>
    </div>
  )
}
