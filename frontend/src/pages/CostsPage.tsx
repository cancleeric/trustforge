import { useEffect, useState } from 'react'
import { getCosts } from '../lib/endpoints'
import type { CostsData, LedgerRunRecord } from '../lib/types'
import { formatUsd } from '../lib/format'
import { ErrorState, LoadingState } from '../components/StatusStates'

function ByModelTable({ data }: { data: CostsData }) {
  const models = Object.keys(data.by_model_detail)
  if (models.length === 0) {
    return <p className="text-sm text-tf-muted">目前尚無按 model 分組的成本紀錄。</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[480px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="tf-num px-3 py-2 text-right font-medium">輸入 tokens</th>
            <th className="tf-num px-3 py-2 text-right font-medium">輸出 tokens</th>
            <th className="tf-num px-3 py-2 text-right font-medium">成本</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {models.map((model) => {
            const detail = data.by_model_detail[model]
            return (
              <tr key={model}>
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
    <div className="overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
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
            <tr key={`${run.ts}-${i}`}>
              <td className="px-3 py-2 text-tf-text2">{run.ts}</td>
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
  const [data, setData] = useState<CostsData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)

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
  }, [offset])

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-6 sm:px-6">
      <h1 className="text-lg font-semibold text-tf-text">成本帳本</h1>

      {loading && <LoadingState label="成本資料載入中…" />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && data && (
        <>
          <div className="rounded-lg border border-tf-border bg-tf-card p-4">
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
            <div className="mt-3 flex items-center justify-between">
              <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>較新</button>
              <button type="button" disabled={offset + data.runs.length >= data.run_count} onClick={() => setOffset(offset + 50)}>較舊</button>
            </div>
          </section>
        </>
      )}
    </main>
  )
}
