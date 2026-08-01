interface Props {
  value: number // 0~1
  label?: string
}

export default function TrustProgressBar({ value, label }: Props) {
  const pct = Math.round(value * 100)
  const level = value >= 0.7 ? 'high' : value >= 0.4 ? 'mid' : 'low'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {label && <span style={{ fontSize: 12, color: 'rgba(220,233,242,0.7)', minWidth: 60 }}>{label}</span>}
      <div className="trustforge-progress" style={{ flex: 1 }}>
        <div className={`trustforge-progress-fill ${level}`} style={{ width: `${pct}%` }} />
      </div>
      <span style={{ fontSize: 11, fontWeight: 600, color: level === 'high' ? '#4dd8e0' : level === 'mid' ? '#fbbf24' : '#ef4444', minWidth: 32, textAlign: 'right' }}>{pct}%</span>
    </div>
  )
}
