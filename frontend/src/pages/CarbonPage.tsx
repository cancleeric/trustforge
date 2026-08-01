import { useEffect, useState } from 'react'
import { getCarbon } from '../lib/endpoints'
import type { CarbonData } from '../lib/types'
import { ErrorState } from '../components/StatusStates'

function MetricCard({ label, value, unit, subtitle }: { label: string; value: string; unit: string; subtitle?: string }) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <p className="text-xs uppercase tracking-wider text-tf-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-tf-text">
        {value} <span className="text-sm font-normal text-tf-muted">{unit}</span>
      </p>
      {subtitle && <p className="mt-0.5 text-xs text-tf-muted">{subtitle}</p>}
    </div>
  )
}

function BreakdownTable({ data }: { data: CarbonData }) {
  const models = Object.keys(data.breakdown_by_model || {})
  if (models.length === 0) {
    return <p className="text-sm text-tf-muted">尚無按模型分組的碳排放紀錄。</p>
  }
  return (
    <div className="hermes-clip overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[480px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">模型類別</th>
            <th className="tf-num px-3 py-2 text-right font-medium">呼叫數</th>
            <th className="tf-num px-3 py-2 text-right font-medium">Tokens</th>
            <th className="tf-num px-3 py-2 text-right font-medium">kWh (est.)</th>
            <th className="tf-num px-3 py-2 text-right font-medium">CO₂e (g)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {models.map((model) => {
            const row = data.breakdown_by_model[model]
            return (
              <tr key={model} className="hermes-row-hover">
                <td className="px-3 py-2 text-tf-text">{model}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{row.calls}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{row.tokens.toLocaleString()}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{row.kwh.toFixed(6)}</td>
                <td className="tf-num px-3 py-2 text-right text-tf-text2">{row.co2e_g.toFixed(4)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function CarbonPage() {
  const [data, setData] = useState<CarbonData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    getCarbon(ctrl.signal).then((res) => {
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        setError(res.error.message)
      }
      setLoading(false)
    }).catch(() => {
      if (!ctrl.signal.aborted) {
        setError('無法載入碳足跡資料')
        setLoading(false)
      }
    })
    return () => ctrl.abort()
  }, [])

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-tf-text">碳足跡</h1>
        <p className="text-sm text-tf-muted">載入中...</p>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-tf-text">碳足跡</h1>
        <ErrorState code="carbon_load_failed" message={error || '資料載入失敗'} />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-tf-text">碳足跡</h1>
        <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
          ESG
        </span>
      </div>

      {/* Metric Cards */}
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="總碳排放"
          value={data.total_estimated_co2e_g < 1000 ? data.total_estimated_co2e_g.toFixed(2) : data.total_estimated_co2e_kg.toFixed(4)}
          unit={data.total_estimated_co2e_g < 1000 ? 'g CO₂e' : 'kg CO₂e'}
          subtitle="estimated"
        />
        <MetricCard
          label="能源消耗"
          value={data.total_estimated_kwh.toFixed(6)}
          unit="kWh"
          subtitle="estimated"
        />
        <MetricCard
          label="總 Token 數"
          value={data.total_tokens.toLocaleString()}
          unit="tokens"
        />
        <MetricCard
          label="LLM 呼叫次數"
          value={data.call_count.toString()}
          unit="calls"
        />
      </div>

      {/* Breakdown Table */}
      <div className="mb-6">
        <h2 className="mb-3 text-sm font-medium text-tf-text">按模型分組</h2>
        <BreakdownTable data={data} />
      </div>

      {/* Methodology & Disclaimer */}
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-800/40 dark:bg-amber-900/10">
        <p className="text-xs font-medium text-amber-800 dark:text-amber-400">估算方法論</p>
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          方法：{data.methodology} | 公式：CO₂e = tokens × energy_per_token(model) × PUE × carbon_intensity(region)
        </p>
        <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
          {data.disclaimer}
        </p>
      </div>
    </div>
  )
}
