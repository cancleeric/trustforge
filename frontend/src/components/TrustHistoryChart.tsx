import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { TooltipContentProps } from 'recharts'
import type { TrustHistoryEntry } from '../lib/types'

interface Props {
  history: TrustHistoryEntry[]
}

function HistoryTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as TrustHistoryEntry
  return (
    <div className="rounded border border-tf-border bg-tf-card p-2 text-xs text-tf-text2 shadow">
      <p className="font-semibold text-tf-text">{label}</p>
      <p className="tf-num">信任分 {p.trust_score.toFixed(2)}</p>
      <p className="tf-num">校準信心 {p.calibrated_confidence.toFixed(2)}</p>
      <p>{p.direction}</p>
    </div>
  )
}

/** PIT（point-in-time）信任分折線圖——新核心 #1 的前端呈現：信任分「隨時間
 * 變化」而非單點快照，大廠（Nansen/Messari 級）沒有這個維度。資料點少
 * （<2 筆，剛開始累積）時 recharts 仍會渲染座標軸/單點，不強行擋掉，讓
 * 使用者看到「目前只有這麼多」而非假裝圖表壞掉。 */
export default function TrustHistoryChart({ history }: Props) {
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={history} margin={{ top: 8, right: 16, left: -16, bottom: 8 }}>
          <CartesianGrid stroke="var(--color-tf-border)" strokeDasharray="3 3" />
          <XAxis dataKey="date" tick={{ fill: 'var(--color-tf-muted)', fontSize: 11 }} />
          <YAxis domain={[0, 1]} tick={{ fill: 'var(--color-tf-muted)', fontSize: 11 }} />
          <Tooltip content={HistoryTooltip} />
          <Line
            type="monotone"
            dataKey="trust_score"
            name="信任分"
            stroke="var(--color-tf-accent)"
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="calibrated_confidence"
            name="校準信心"
            stroke="var(--color-tf-good)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={{ r: 2 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
