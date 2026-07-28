/**
 * WhaleActivityPanel — 右軌即時鯨魚信號卡
 *
 * 顯示最近 1 小時的 BTC 大額轉帳摘要：
 * - 交易所淨流入/流出 + 方向指標
 * - 大額轉帳筆數
 * - 最大單筆金額
 * - 最近 3 筆摘要
 */

export interface WhaleSummary {
  coin: string
  period_hours: number
  total_count: number
  total_usd: number
  net_exchange_flow_usd: number
  exchange_inflow_usd: number
  exchange_outflow_usd: number
  max_single_usd: number
  whale_transfer_count: number
  exchange_inflow_count: number
  exchange_outflow_count: number
  recent_transfers: WhaleTransfer[]
  updated_at: string | null
  signal: string
  signal_label: string
}

export interface WhaleTransfer {
  amount_usd: number
  coin: string
  from: string
  to: string
  direction: string
  ts: number
}

function formatUsd(value: number): string {
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`
  return `$${value.toFixed(0)}`
}

function directionIcon(direction: string): string {
  if (direction === 'exchange_outflow') return '↗'
  if (direction === 'exchange_inflow') return '↘'
  return '↔'
}

function directionLabel(direction: string): string {
  if (direction === 'exchange_outflow') return '流出'
  if (direction === 'exchange_inflow') return '流入'
  return '轉帳'
}

interface Props {
  summary: WhaleSummary | null
  loading?: boolean
}

export default function WhaleActivityPanel({ summary, loading }: Props) {
  if (loading) {
    return (
      <div className="rounded-lg border border-tf-border bg-tf-card/50 p-3">
        <div className="text-xs text-tf-muted">載入鯨魚動態中…</div>
      </div>
    )
  }

  if (!summary || summary.total_count === 0) {
    return (
      <div className="rounded-lg border border-tf-border bg-tf-card/50 p-3">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-tf-muted">
          <span>🐋</span>
          <span>鯨魚動態</span>
        </div>
        <div className="mt-1.5 text-[0.68rem] text-tf-muted">暫無大額轉帳紀錄</div>
      </div>
    )
  }

  const netFlow = summary.net_exchange_flow_usd
  const isOutflow = netFlow < 0
  const flowColor = isOutflow ? 'var(--color-tf-good)' : 'var(--color-tf-warn)'
  const flowIcon = isOutflow ? '↗' : '↘'
  const flowLabel = isOutflow ? '淨流出（囤積）' : '淨流入（賣壓）'

  return (
    <div className="rounded-lg border border-tf-border bg-tf-card/50 p-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-tf-text">
          <span>🐋</span>
          <span>{summary.coin} 鯨魚動態</span>
          <span className="text-[0.6rem] text-tf-muted">（最近{summary.period_hours}h）</span>
        </div>
        {summary.updated_at && (
          <span className="text-[0.58rem] text-tf-muted">
            {new Date(summary.updated_at).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {/* Stats row */}
      <div className="mt-2 grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-[0.6rem] text-tf-muted">交易所淨流</div>
          <div className="text-xs font-bold" style={{ color: flowColor }}>
            {flowIcon} {formatUsd(Math.abs(netFlow))}
          </div>
          <div className="text-[0.55rem] text-tf-muted">{flowLabel}</div>
        </div>
        <div>
          <div className="text-[0.6rem] text-tf-muted">筆數</div>
          <div className="text-xs font-bold text-tf-text">{summary.total_count}</div>
          <div className="text-[0.55rem] text-tf-muted">大額轉帳</div>
        </div>
        <div>
          <div className="text-[0.6rem] text-tf-muted">最大單筆</div>
          <div className="text-xs font-bold text-tf-text">{formatUsd(summary.max_single_usd)}</div>
          <div className="text-[0.55rem] text-tf-muted">USD</div>
        </div>
      </div>

      {/* Recent transfers */}
      {summary.recent_transfers.length > 0 && (
        <div className="mt-2 border-t border-tf-border pt-2">
          <div className="text-[0.6rem] text-tf-muted mb-1">最近動態</div>
          <div className="space-y-0.5">
            {summary.recent_transfers.slice(0, 3).map((tx, i) => (
              <div key={i} className="flex items-center justify-between text-[0.65rem]">
                <span className="text-tf-text">
                  {formatUsd(tx.amount_usd)}
                </span>
                <span className="text-tf-muted truncate max-w-[120px]">
                  {tx.from === 'unknown' ? '???' : tx.from} → {tx.to === 'unknown' ? '???' : tx.to}
                </span>
                <span style={{ color: tx.direction === 'exchange_outflow' ? 'var(--color-tf-good)' : tx.direction === 'exchange_inflow' ? 'var(--color-tf-warn)' : 'var(--color-tf-muted)' }}>
                  {directionIcon(tx.direction)} {directionLabel(tx.direction)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Signal badge */}
      <div className="mt-2 flex items-center gap-1.5">
        <span
          className="inline-block rounded px-1.5 py-0.5 text-[0.6rem] font-medium"
          style={{
            backgroundColor: isOutflow ? 'rgba(77,216,224,.12)' : 'rgba(232,179,77,.12)',
            color: flowColor,
          }}
        >
          {summary.signal_label}
        </span>
      </div>
    </div>
  )
}
