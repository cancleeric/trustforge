import { useEffect, useState } from 'react'
import { getCosts } from '../lib/endpoints'
import type { CostsData, LedgerRunRecord } from '../lib/types'
import { formatTimestamp, formatUsd } from '../lib/format'
import { ErrorState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'
import GlossaryTerm from '../components/GlossaryTerm'

function ByModelTable({ data }: { data: CostsData }) {
  const models = Object.keys(data.by_model_detail)
  if (models.length === 0) {
    return <p className="text-sm text-tf-muted">目前尚無按 model 分組的成本紀錄。</p>
  }
  return (
    <div className="hermes-clip overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[480px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="tf-num px-3 py-2 text-right font-medium">輸入 <GlossaryTerm term="tokenUsage" label="tokens" compact /></th>
            <th className="tf-num px-3 py-2 text-right font-medium">輸出 <GlossaryTerm term="tokenUsage" label="tokens" compact /></th>
            <th className="tf-num px-3 py-2 text-right font-medium">成本</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {models.map((model) => {
            const detail = data.by_model_detail[model]
            return (
               <tr key={model} className="hermes-row-hover">
                <td className="px-3 py-2 text-tf-text">{model}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{detail.tokens_in.toLocaleString()}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{detail.tokens_out.toLocaleString()}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{formatUsd(detail.cost_usd)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// `data.runs` is one page of the append-only ledger, newest first.
// （非全量帳本），這裡照原始寫入順序反轉成「最新在前」再渲染，跟 SSR
// `_render_costs_page()` 的 `list(reversed(runs))` 邏輯一致。
function RecentRunsTable({ runs }: { runs: LedgerRunRecord[] }) {
  if (runs.length === 0) {
    return <p className="text-sm text-tf-muted">目前尚無 run 紀錄。</p>
  }
  const recent = runs
  return (
    <div className="hermes-clip overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[480px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">時間</th>
            <th className="px-3 py-2 font-medium">幣種</th>
            <th className="px-3 py-2 font-medium">類型</th>
            <th className="tf-num px-3 py-2 text-right font-medium">呼叫數</th>
            <th className="tf-num px-3 py-2 text-right font-medium">成本</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {recent.map((run, i) => (
               <tr key={`${run.ts}-${i}`} className="hermes-row-hover">
              <td className="tf-num whitespace-nowrap px-3 py-2 text-tf-text2" title={run.ts}>{formatTimestamp(run.ts)}</td>
              <td className="px-3 py-2 text-tf-text2">{run.coin ?? '—'}</td>
              <td className="px-3 py-2 text-tf-text2">
                {run.question_type ?? '—'}
                {run.offline && <span className="ml-1 text-tf-muted">(離線)</span>}
              </td>
              <td className="tf-num px-3 py-2 text-right text-tf-text2">{run.calls.length}</td>
              <td className="tf-num px-3 py-2 text-right text-tf-text2">{formatUsd(run.total_cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function CostsPage() {
  const { setData: setHologramData } = useBridgeHologram()
  const [data, setData] = useState<CostsData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [retryNonce, setRetryNonce] = useState(0)

  useEffect(() => {
    setHologramData(data ? {
      primaryValue: data.total_cost_usd,
      total: data.run_count,
      status: `${Object.keys(data.by_model).length} MODELS`,
    } : null)
    return () => setHologramData(null)
  }, [data, setHologramData])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getCosts(offset, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        setData(null)
        setError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
  }, [offset, retryNonce])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setRetryNonce((value) => value + 1), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Append-only ledger</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">成本帳本</h1>
        <p className="mt-1 text-sm text-tf-text2">跨 run 的 LLM 呼叫成本與 token 用量；明細以分頁讀取，歷史資料不會被截斷。</p>
      </div>

      {loading && null}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && data && (
        <>
          <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
            <p className="text-xs text-tf-muted">累計總花費（跨 run，Bedrock/LLM 呼叫成本）</p>
            <p className="tf-num mt-1 text-3xl font-bold text-tf-text">{formatUsd(data.total_cost_usd)}</p>
            <p className="mt-1 text-xs text-tf-muted">累計 {data.run_count.toLocaleString()} 筆 run 紀錄</p>
          </div>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-tf-text">按 Model 分組</h3>
            <ByModelTable data={data} />
          </section>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-tf-text">
              Per-run 明細（第 {(data.offset ?? 0) + 1}–{Math.min((data.offset ?? 0) + data.runs.length, data.run_count)} 筆，共 {data.run_count.toLocaleString()} 筆）
            </h3>
            <RecentRunsTable runs={data.runs} />
            <div className="mt-3 flex items-center justify-between gap-3">
              <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))} className="rounded-[7px] border border-tf-border px-[10px] py-[10px] text-xs font-semibold text-tf-muted disabled:cursor-not-allowed disabled:opacity-40">較新</button>
              <span className="text-xs text-tf-muted">每頁 50 筆</span>
              <button type="button" disabled={offset + data.runs.length >= data.run_count} onClick={() => setOffset(offset + 50)} className="rounded-[7px] border border-tf-border px-[10px] py-[10px] text-xs font-semibold text-tf-muted disabled:cursor-not-allowed disabled:opacity-40">較舊</button>
            </div>
          </section>
        </>
      )}
    </main>
  )
}
