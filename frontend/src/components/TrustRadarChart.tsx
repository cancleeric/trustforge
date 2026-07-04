import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { TooltipContentProps } from 'recharts'
import type { TrustRadar } from '../lib/types'
import { SingleSourceBadge } from './Badges'

interface Props {
  radar: TrustRadar
}

interface ChartPoint {
  label: string
  trust: number
  nSources: number
  nEvidence: number
  singleSource: boolean
}

function RadarTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as ChartPoint
  return (
    <div className="rounded border border-tf-border bg-tf-card p-2 text-xs text-tf-text2 shadow">
      <p className="font-semibold text-tf-text">{p.label}</p>
      <p className="tf-num">信任 {p.trust.toFixed(2)}</p>
      <p>{p.nSources} 個獨立來源 · {p.nEvidence} 筆證據</p>
      {p.singleSource && <p className="text-tf-warn">&#9888; 單一來源，尚未跨源互證</p>}
    </div>
  )
}

export default function TrustRadarChart({ radar }: Props) {
  const dims = Object.values(radar)
  const withData = dims.filter((d) => d.has_data && d.trust !== null)
  const withoutData = dims.filter((d) => !d.has_data)

  if (withData.length === 0) {
    return (
      <div className="rounded-lg border border-tf-border bg-tf-card p-4">
        <h3 className="mb-2 text-sm font-semibold text-tf-text">多維度信任雷達</h3>
        <p className="text-xs text-tf-muted">目前尚無任何維度累積足夠資料，暫無法繪製雷達圖。</p>
      </div>
    )
  }

  const chartData: ChartPoint[] = withData.map((d) => ({
    label: d.label,
    trust: d.trust ?? 0,
    nSources: d.n_sources,
    nEvidence: d.n_evidence,
    singleSource: Boolean(d.single_source),
  }))

  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-2 text-sm font-semibold text-tf-text">多維度信任雷達</h3>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={chartData} outerRadius="70%">
            <PolarGrid stroke="var(--color-tf-border)" />
            <PolarAngleAxis dataKey="label" tick={{ fill: 'var(--color-tf-muted)', fontSize: 11 }} />
            <Radar
              dataKey="trust"
              stroke="var(--color-tf-accent)"
              fill="var(--color-tf-accent)"
              fillOpacity={0.35}
              isAnimationActive={false}
            />
            <Tooltip content={RadarTooltip} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-2">
        {chartData
          .filter((d) => d.singleSource)
          .map((d) => (
            <li key={d.label} className="flex items-center gap-1 text-[0.7rem] text-tf-muted">
              {d.label} <SingleSourceBadge />
            </li>
          ))}
      </ul>
      {withoutData.length > 0 && (
        <p className="mt-2 text-[0.7rem] text-tf-muted">
          尚無資料的維度：{withoutData.map((d) => d.label).join('、')}
        </p>
      )}
    </div>
  )
}
