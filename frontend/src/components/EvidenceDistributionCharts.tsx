import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Evidence } from '../lib/types'
import { sourceDisplayName } from '../lib/sourceBrand'

const COLORS = ['#4dd8e0', '#7bd88f', '#f0c75e', '#ef6b73', '#9e8cff', '#59a8ff']

export default function EvidenceDistributionCharts({ evidence }: { evidence: Evidence[] }) {
  const sourceScores = evidence.map((item) => ({ name: sourceDisplayName(item.source), trust: Math.round(item.trust * 100) }))
    .sort((a, b) => b.trust - a.trust).slice(0, 10)
  const kinds = Object.entries(evidence.reduce<Record<string, number>>((counts, item) => {
    counts[item.kind] = (counts[item.kind] ?? 0) + 1
    return counts
  }, {})).map(([name, value]) => ({ name, value }))
  if (!evidence.length) return null
  return (
    <div className="grid gap-4 lg:grid-cols-2" aria-label="證據圖表摘要">
      <section className="h-72 rounded-lg border border-tf-border bg-tf-card p-3">
        <h3 className="mb-2 text-sm font-semibold text-tf-text">各來源信任分</h3>
        <ResponsiveContainer width="100%" height="90%"><BarChart data={sourceScores} layout="vertical" margin={{ left: 10 }}>
          <CartesianGrid stroke="var(--color-tf-border)" horizontal={false} /><XAxis type="number" domain={[0, 100]} stroke="var(--color-tf-muted)" />
          <YAxis type="category" dataKey="name" width={90} tick={{ fill: 'var(--color-tf-muted)', fontSize: 11 }} /><Tooltip /><Bar dataKey="trust" fill="var(--color-tf-accent)" radius={[0, 4, 4, 0]} />
        </BarChart></ResponsiveContainer>
      </section>
      <section className="h-72 rounded-lg border border-tf-border bg-tf-card p-3">
        <h3 className="mb-2 text-sm font-semibold text-tf-text">來源類型佔比</h3>
        <ResponsiveContainer width="100%" height="90%"><PieChart><Pie data={kinds} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="72%" label>
          {kinds.map((item, index) => <Cell key={item.name} fill={COLORS[index % COLORS.length]} />)}
        </Pie><Tooltip /></PieChart></ResponsiveContainer>
      </section>
    </div>
  )
}
