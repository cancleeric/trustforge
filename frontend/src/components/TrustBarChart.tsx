interface BarItem {
  label: string
  value: number // 0~1
}

interface Props {
  items: BarItem[]
}

export default function TrustBarChart({ items }: Props) {
  return (
    <div className="trustforge-bar-chart">
      {items.map((item, i) => (
        <div key={i} className="trustforge-bar-row">
          <span className="trustforge-bar-label">{item.label}</span>
          <div className="trustforge-bar-track">
            <div className="trustforge-bar-fill" style={{ width: `${Math.round(item.value * 100)}%` }} />
          </div>
          <span className="trustforge-bar-value">{item.value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}
