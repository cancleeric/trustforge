import { useEffect, useState } from 'react'
import { getCosts } from '../lib/endpoints'
import type { CostsData } from '../lib/types'
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

export default function CostsPage() {
  const [data, setData] = useState<CostsData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getCosts(controller.signal).then((res) => {
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
  }, [])

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
            <p className="mt-1 text-xs text-tf-muted">累計 {data.runs.length.toLocaleString()} 筆 run 紀錄</p>
          </div>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-tf-text">按 Model 分組</h3>
            <ByModelTable data={data} />
          </section>
        </>
      )}
    </main>
  )
}
