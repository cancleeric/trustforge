import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getCosts, getStatus } from '../lib/endpoints'
import type { CostsData, FreshnessEntry, StatusData } from '../lib/types'
import { formatAge, formatEpoch, formatUptime, formatUsd } from '../lib/format'
import { DegradedBadge, FreshnessStatusBadge } from '../components/Badges'
import { ErrorState, LoadingState } from '../components/StatusStates'

// 鮮度矩陣預設排序：把需要注意的格子排前面（missing 沒資料最該關注、
// stale 次之、fresh 最後），而不是照 API 回傳原始順序（逐 source × coin
// 迴圈生成，跟嚴重度無關）——世界級監控面板通則：異常優先於正常。
const STATUS_RANK: Record<FreshnessEntry['status'], number> = { missing: 0, stale: 1, fresh: 2 }

function sortedEntries(entries: FreshnessEntry[]): FreshnessEntry[] {
  return [...entries].sort((a, b) => {
    const rankDiff = STATUS_RANK[a.status] - STATUS_RANK[b.status]
    if (rankDiff !== 0) return rankDiff
    if (a.source !== b.source) return a.source.localeCompare(b.source)
    return a.coin.localeCompare(b.coin)
  })
}

function StatCard({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <p className="text-xs text-tf-muted">{label}</p>
      <p className="tf-num mt-1 text-2xl font-bold" style={color ? { color } : undefined}>
        {value}
      </p>
    </div>
  )
}

function FreshnessMatrix({ entries }: { entries: FreshnessEntry[] }) {
  const rows = sortedEntries(entries)
  return (
    <div className="overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[560px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">來源</th>
            <th className="px-3 py-2 font-medium">幣種</th>
            <th className="px-3 py-2 font-medium">狀態</th>
            <th className="tf-num px-3 py-2 text-right font-medium">最後更新</th>
            <th className="tf-num px-3 py-2 text-right font-medium">寫入時間</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {rows.map((e) => (
            <tr key={`${e.source}-${e.coin}`}>
              <td className="whitespace-nowrap px-3 py-2 text-tf-text">{e.source}</td>
              <td className="px-3 py-2 text-tf-text2">{e.coin}</td>
              <td className="px-3 py-2">
                <FreshnessStatusBadge status={e.status} />
              </td>
              <td className="tf-num px-3 py-2 text-right text-tf-muted">{formatAge(e.age_seconds)}</td>
              <td className="tf-num px-3 py-2 text-right text-tf-muted">{formatEpoch(e.fetched_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CostSummaryCard({
  costs,
  costsError,
  costsLoading,
}: {
  costs: CostsData | null
  costsError: { code: string; message: string } | null
  costsLoading: boolean
}) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-tf-text">成本摘要</h3>
        <Link to="/costs" className="text-xs text-tf-link underline">
          查看完整成本明細 &#8594;
        </Link>
      </div>
      {/* 成本是輔助區塊，自己的 loading/error 只影響這張卡，不阻塞已經
          顯示的狀態主內容——帳本變大或成本 API 延遲時，監控頁最需要看
          的鮮度矩陣/cache backend 狀態必須立即可見。 */}
      {costsLoading && <p className="text-xs text-tf-muted">成本資料載入中…</p>}
      {!costsLoading && costsError && <p className="text-xs text-tf-muted">成本資料暫時無法讀取（{costsError.code}）。</p>}
      {!costsLoading && !costsError && costs && (
        <div className="flex items-baseline gap-2">
          <span className="tf-num text-2xl font-bold text-tf-text">{formatUsd(costs.total_cost_usd)}</span>
          <span className="text-xs text-tf-muted">
            累計花費，共 {costs.run_count.toLocaleString()} 個 run
          </span>
        </div>
      )}
    </div>
  )
}

export default function StatusPage() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [statusError, setStatusError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  const [costs, setCosts] = useState<CostsData | null>(null)
  const [costsError, setCostsError] = useState<{ code: string; message: string } | null>(null)
  const [costsLoading, setCostsLoading] = useState(true)

  // 狀態獨立請求：主 loading 只取決於 getStatus，資料一到就立即渲染，
  // 不受成本 API（無界 runs 陣列、可能延遲/逾時）拖累。
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setStatusError(null)
    getStatus(controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setStatus(res.data)
        setStatusError(null)
      } else {
        setStatus(null)
        setStatusError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
  }, [])

  // 成本獨立請求：各自的 AbortController/timeout，pending/timeout/error
  // 只反映在 CostSummaryCard 自己的區塊，不影響上面已渲染的狀態內容。
  useEffect(() => {
    const controller = new AbortController()
    setCostsLoading(true)
    setCostsError(null)
    getCosts(controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setCostsLoading(false)
      if (res.ok) {
        setCosts(res.data)
        setCostsError(null)
      } else {
        setCosts(null)
        setCostsError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
  }, [])

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 sm:px-6">
      <h1 className="text-lg font-semibold text-tf-text">系統狀態</h1>

      {loading && <LoadingState label="狀態載入中…" />}
      {!loading && statusError && <ErrorState code={statusError.code} message={statusError.message} />}
      {!loading && !statusError && status && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-tf-border bg-tf-card p-4">
            <span className="text-sm text-tf-text2">版本 {status.version}</span>
            <span className="tf-num text-sm text-tf-text2">運行時間 {formatUptime(status.uptime_seconds)}</span>
            <span className="text-sm text-tf-text2">
              Bedrock：{status.bedrock_capable ? '可用' : '未啟用'}
            </span>
          </div>

          <div className="rounded-lg border border-tf-border bg-tf-card p-4">
            <h3 className="mb-2 text-sm font-semibold text-tf-text">Cache Backend</h3>
            <div className="flex flex-wrap items-center gap-2 text-sm text-tf-text2">
              <span>
                {status.cache_backend.name}（實際服務：{status.cache_backend.active_backend}）
              </span>
              {status.cache_backend.degraded ? (
                <DegradedBadge />
              ) : (
                <span className="inline-flex items-center rounded-full border border-tf-good px-2 py-0.5 text-xs font-semibold text-tf-good">
                  正常
                </span>
              )}
            </div>
          </div>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-tf-text">連接器資料鮮度</h3>
            <div className="mb-3 grid grid-cols-3 gap-3">
              <StatCard label="新鮮" value={status.freshness.fresh} color="var(--color-tf-good)" />
              <StatCard label="過期" value={status.freshness.stale} color="var(--color-tf-warn)" />
              <StatCard label="缺席" value={status.freshness.missing} color="var(--color-tf-muted)" />
            </div>
            <FreshnessMatrix entries={status.freshness.entries} />
          </section>

          <CostSummaryCard costs={costs} costsError={costsError} costsLoading={costsLoading} />
        </>
      )}
    </main>
  )
}
