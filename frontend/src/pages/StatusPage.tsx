import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getCosts, getStatus } from '../lib/endpoints'
import type { CostsData, FreshnessEntry, StatusData } from '../lib/types'
import { formatAge, formatEpoch, formatUptime, formatUsd } from '../lib/format'
import { DegradedBadge, FreshnessStatusBadge } from '../components/Badges'
import { ErrorState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'

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
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
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
    <div className="hermes-clip overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
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
              <tr key={`${e.source}-${e.coin}`} className="hermes-row-hover">
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

export function HoyaBitIntegrationNotice() {
  return (
    <div className="mb-3 rounded-lg border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_8%,transparent)] p-3 text-xs" role="status">
      <p className="font-semibold text-tf-warn">HOYA BIT：部分接</p>
      <p className="mt-1 text-tf-text2">
        官方歷史 OHLCV 基準已提供；線上 ticker 仍須部署端設定官方 endpoint，depth／orderbook／trades 則等待官方合約。未設定時不會列為即時真值來源。
      </p>
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
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
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
  const { setData: setHologramData } = useBridgeHologram()
  const [status, setStatus] = useState<StatusData | null>(null)
  const [statusError, setStatusError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  const [costs, setCosts] = useState<CostsData | null>(null)
  const [costsError, setCostsError] = useState<{ code: string; message: string } | null>(null)
  const [costsLoading, setCostsLoading] = useState(true)
  const [statusRetryNonce, setStatusRetryNonce] = useState(0)
  const [costsRetryNonce, setCostsRetryNonce] = useState(0)

  useEffect(() => {
    if (!status) {
      setHologramData(null)
      return
    }
    const total = status.freshness.fresh + status.freshness.stale + status.freshness.missing
    setHologramData({
      total,
      primaryValue: total ? status.freshness.fresh / total : 0,
      status: status.cache_backend.degraded ? 'DEGRADED' : 'UPLINK NOMINAL',
    })
    return () => setHologramData(null)
  }, [status, setHologramData])

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
  }, [statusRetryNonce])

  useEffect(() => {
    if (statusError?.code !== 'network_error') return
    const timer = window.setTimeout(() => setStatusRetryNonce((value) => value + 1), 1800)
    return () => window.clearTimeout(timer)
  }, [statusError])

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
  }, [costsRetryNonce])

  useEffect(() => {
    if (costsError?.code !== 'network_error') return
    const timer = window.setTimeout(() => setCostsRetryNonce((value) => value + 1), 1800)
    return () => window.clearTimeout(timer)
  }, [costsError])

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,#0b1420 0%,#050810 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Runtime observability</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">系統狀態</h1>
        <p className="mt-1 text-sm text-tf-text2">快取連線與來源新鮮度的即時監控；異常資料會排在清單前方。</p>
      </div>

      {loading && null}
      {!loading && statusError && <ErrorState code={statusError.code} message={statusError.message} />}
      {!loading && !statusError && status && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="hermes-clip border border-tf-border bg-tf-card p-3"><p className="text-xs text-tf-muted">部署版本</p><p className="mt-1 font-mono text-sm font-semibold text-tf-text">{status.version}</p></div>
            <div className="hermes-clip border border-tf-border bg-tf-card p-3"><p className="text-xs text-tf-muted">服務運行時間</p><p className="tf-num mt-1 text-sm font-semibold text-tf-text">{formatUptime(status.uptime_seconds)}</p></div>
            <div className="hermes-clip border border-tf-border bg-tf-card p-3"><p className="text-xs text-tf-muted">LLM runtime</p><p className="mt-1 text-sm font-semibold text-tf-text">Bedrock：{status.bedrock_capable ? '可用' : '未啟用'}</p></div>
          </div>

          <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
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
            <HoyaBitIntegrationNotice />
            {status.freshness.fresh === 0 && status.freshness.stale === 0 && status.freshness.missing > 0 && (
              <div className="mb-3 border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_8%,transparent)] p-3 text-xs text-tf-warn" role="status">
                <p className="font-semibold">服務正常，但尚未收到來源快照</p>
                <p className="mt-1 text-tf-text2">這是「尚無資料」，不是來源故障。可先回首頁執行一次分析，或等待排程累積資料。</p>
                <a href="/" className="mt-2 inline-flex rounded border border-tf-warn px-2 py-1 font-semibold text-tf-warn no-underline">回首頁選擇分析任務</a>
              </div>
            )}
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
