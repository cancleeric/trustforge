/**
 * WhaleHistoryPanel — 大額轉帳歷程面板
 *
 * 顯示 1天/7天/30天 的鯨魚大額轉帳歷程：
 * - 時間範圍切換 tabs
 * - 統計摘要卡（4 個數字）
 * - 趨勢柱狀圖（純 SVG）
 * - 明細表（時間/金額/來源/目的/方向）
 */

import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiFetch, DEFAULT_TIMEOUT_MS } from '../lib/apiClient'
import type { ApiEnvelope } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { useBridgeHologram } from './BridgeHologramContext'

interface WhaleHistorySummary {
  total_count: number
  total_usd: number
  net_exchange_flow_usd: number
  max_single_usd: number
}

interface TimelineBucket {
  bucket: string
  count: number
  total_usd: number
  net_flow_usd: number
}

interface HistoryTransfer {
  amount_usd: number
  amount: number
  coin: string
  from: string
  to: string
  direction: string
  ts: number
  tx_url: string
}

interface WhaleHistoryData {
  coin: string
  days: number
  available_since: string | null
  summary: WhaleHistorySummary
  timeline: TimelineBucket[]
  transfers: HistoryTransfer[]
}

function formatUsd(value: number): string {
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`
  return `$${value.toFixed(0)}`
}

function directionBadge(direction: string): { icon: string; label: string; color: string } {
  if (direction === 'exchange_outflow') return { icon: '↗', label: '流出', color: 'var(--color-tf-good)' }
  if (direction === 'exchange_inflow') return { icon: '↘', label: '流入', color: 'var(--color-tf-warn)' }
  return { icon: '↔', label: '轉帳', color: 'var(--color-tf-muted)' }
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

// Simple SVG bar chart
function TimelineChart({ timeline }: { timeline: TimelineBucket[] }) {
  if (timeline.length === 0) return null
  const maxCount = Math.max(...timeline.map((b) => b.count), 1)
  const barWidth = Math.max(4, Math.min(20, Math.floor(280 / timeline.length) - 2))
  const chartWidth = timeline.length * (barWidth + 2) + 20
  const chartHeight = 60

  // Reverse so oldest is on left
  const bars = [...timeline].reverse()

  return (
    <div className="overflow-x-auto">
      <svg width={chartWidth} height={chartHeight + 20} className="block">
        {bars.map((bucket, i) => {
          const h = (bucket.count / maxCount) * chartHeight
          const x = 10 + i * (barWidth + 2)
          const y = chartHeight - h
          const fill = bucket.net_flow_usd < 0 ? 'rgba(77,216,224,.6)' : bucket.net_flow_usd > 0 ? 'rgba(232,179,77,.6)' : 'rgba(140,190,210,.3)'
          return (
            <g key={i}>
              <rect x={x} y={y} width={barWidth} height={h} rx={1} fill={fill} />
              <title>{bucket.bucket}: {bucket.count} 筆, {formatUsd(bucket.total_usd)}</title>
            </g>
          )
        })}
        {/* x-axis labels: first and last */}
        {bars.length > 0 && (
          <>
            <text x={10} y={chartHeight + 14} fontSize={8} fill="var(--color-tf-muted)">{bars[0].bucket.slice(5)}</text>
            <text x={chartWidth - 50} y={chartHeight + 14} fontSize={8} fill="var(--color-tf-muted)" textAnchor="end">{bars[bars.length - 1].bucket.slice(5)}</text>
          </>
        )}
      </svg>
    </div>
  )
}

type DaysOption = 1 | 7 | 30

interface Props {
  coin?: string
}

export default function WhaleHistoryPanel({ coin: propCoin }: Props) {
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams] = useSearchParams()
  const coin = propCoin || searchParams.get('coin')?.toUpperCase() || COIN_POOL[0]
  const [days, setDays] = useState<DaysOption>(7)
  const [data, setData] = useState<WhaleHistoryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setHologramData(data ? {
      primaryLabel: data.coin,
      primaryValue: data.summary.net_exchange_flow_usd,
      secondaryValue: data.summary.max_single_usd,
      total: data.summary.total_count,
      points: data.timeline.map((bucket) => bucket.net_flow_usd),
      status: `${data.days} DAYS · ${data.transfers.length} RETURNED`,
      workspaceStageMetrics: [
        { metric: data.summary.total_count.toLocaleString(), unit: '已記錄大額活動', status: 'completed' },
        {
          metric: data.transfers.filter((transfer) => transfer.direction.includes('exchange_')).length.toLocaleString(),
          unit: '已分類交易所流向',
          status: 'completed',
        },
        { metric: formatUsd(data.summary.net_exchange_flow_usd), unit: '交易所淨流量', status: 'completed' },
        {
          metric: data.transfers.length.toLocaleString(),
          unit: '本次回傳大額明細',
          status: 'completed',
          facts: [{ label: '最大單筆', value: formatUsd(data.summary.max_single_usd) }],
        },
        { metric: data.timeline.length.toLocaleString(), unit: `${data.days} 日趨勢時間桶`, status: 'completed' },
      ],
    } : null)
    return () => setHologramData(null)
  }, [data, setHologramData])

  const fetchHistory = useCallback(async (c: string, d: DaysOption, signal: AbortSignal) => {
    setLoading(true)
    setError(null)
    const valid = (value: unknown): value is WhaleHistoryData => {
      if (!value || typeof value !== 'object') return false
      const v = value as Record<string, unknown>
      return typeof v.coin === 'string' && typeof v.days === 'number'
    }
    const result: ApiEnvelope<WhaleHistoryData> = await apiFetch<WhaleHistoryData>(
      '/api/whale-history', { coin: c, days: String(d) }, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS },
    )
    if (signal.aborted) return
    if (result.ok) {
      setData(result.data)
    } else {
      setError(result.error.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void fetchHistory(coin, days, controller.signal)
    return () => controller.abort()
  }, [coin, days, fetchHistory])

  const tabClass = (d: DaysOption) =>
    `px-2.5 py-1 text-xs rounded cursor-pointer transition-colors ${days === d ? 'bg-tf-accent text-white font-semibold' : 'text-tf-muted hover:text-tf-text'}`

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header + tabs */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tf-text">🐋 {coin} 大額轉帳歷程</h2>
        <div className="flex gap-1 rounded-lg border border-tf-border bg-tf-card p-0.5">
          <button type="button" className={tabClass(1)} onClick={() => setDays(1)}>1天</button>
          <button type="button" className={tabClass(7)} onClick={() => setDays(7)}>7天</button>
          <button type="button" className={tabClass(30)} onClick={() => setDays(30)}>30天</button>
        </div>
      </div>

      {loading && <div className="text-xs text-tf-muted">載入中…</div>}
      {error && <div className="text-xs text-tf-bad">載入失敗：{error}</div>}

      {data && !loading && (
        <>
          {/* Available since notice */}
          {data.available_since && (
            <div className="text-[0.6rem] text-tf-muted">
              資料起始：{new Date(data.available_since).toLocaleDateString('zh-TW')}
              （排程器開始累積後自動擴展）
            </div>
          )}

          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <SummaryCard label="總筆數" value={String(data.summary.total_count)} />
            <SummaryCard label="總流量" value={formatUsd(data.summary.total_usd)} />
            <SummaryCard
              label="交易所淨流"
              value={formatUsd(Math.abs(data.summary.net_exchange_flow_usd))}
              sub={data.summary.net_exchange_flow_usd < 0 ? '淨流出（囤積）' : '淨流入（賣壓）'}
              color={data.summary.net_exchange_flow_usd < 0 ? 'var(--color-tf-good)' : 'var(--color-tf-warn)'}
            />
            <SummaryCard label="最大單筆" value={formatUsd(data.summary.max_single_usd)} />
          </div>

          {/* Timeline chart */}
          {data.timeline.length > 0 && (
            <div className="rounded-lg border border-tf-border bg-tf-card p-3">
              <div className="text-[0.65rem] text-tf-muted mb-1">
                {days <= 1 ? '每小時' : '每日'}轉帳量（柱高=筆數，cyan=淨流出，amber=淨流入）
              </div>
              <TimelineChart timeline={data.timeline} />
            </div>
          )}

          {/* Transfer table */}
          {data.transfers.length > 0 && (
            <div className="rounded-lg border border-tf-border bg-tf-card overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-tf-border text-tf-muted">
                    <th className="px-3 py-2 font-medium">時間</th>
                    <th className="px-3 py-2 font-medium">金額</th>
                    <th className="px-3 py-2 font-medium">來源</th>
                    <th className="px-3 py-2 font-medium">目的</th>
                    <th className="px-3 py-2 font-medium">方向</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-tf-border">
                  {data.transfers.slice(0, 50).map((tx, i) => {
                    const badge = directionBadge(tx.direction)
                    return (
                      <tr key={i} className="hermes-row-hover">
                        <td className="px-3 py-1.5 text-tf-muted whitespace-nowrap">{formatTime(tx.ts)}</td>
                        <td className="px-3 py-1.5 font-medium text-tf-text">{formatUsd(tx.amount_usd)}</td>
                        <td className="px-3 py-1.5 text-tf-text truncate max-w-[80px]">{tx.from === 'unknown' ? '—' : tx.from}</td>
                        <td className="px-3 py-1.5 text-tf-text truncate max-w-[80px]">{tx.to === 'unknown' ? '—' : tx.to}</td>
                        <td className="px-3 py-1.5 whitespace-nowrap" style={{ color: badge.color }}>
                          {badge.icon} {badge.label}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {data.transfers.length > 50 && (
                <div className="px-3 py-2 text-[0.6rem] text-tf-muted border-t border-tf-border">
                  顯示前 50 筆（共 {data.transfers.length} 筆）
                </div>
              )}
            </div>
          )}

          {data.transfers.length === 0 && data.summary.total_count === 0 && (
            <div className="text-xs text-tf-muted text-center py-6">
              此期間尚無累積的大額轉帳紀錄。<br />
              排程器持續運行後會自動累積歷史資料。
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SummaryCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card/50 p-2.5 text-center">
      <div className="text-[0.6rem] text-tf-muted">{label}</div>
      <div className="text-sm font-bold text-tf-text" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="text-[0.55rem] text-tf-muted">{sub}</div>}
    </div>
  )
}
