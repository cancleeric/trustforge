import { useCallback, useEffect, useState } from 'react'

interface PerCoinStat {
  total: number
  has_direction: number
}

interface TrainingStatusData {
  training_data: {
    total_records: number
    has_direction: number
    direction_ratio: number
    per_coin: Record<string, PerCoinStat>
  }
  backfill: {
    mode: string
    is_running: boolean
    completed: number
    total: number
    progress_pct: number
  } | null
  upgrade_threshold: {
    target: number
    current: number
    met: boolean
    pct: number
  }
}

type StatusLight = 'green' | 'yellow' | 'neutral' | 'red'

type AvailabilityState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'not_enabled'; diagnostic: string }
  | { kind: 'unavailable'; diagnostic: string }
  | { kind: 'error'; message: string; diagnostic: string }

function getStatusLight(data: TrainingStatusData | null): StatusLight {
  if (!data) return 'neutral'
  if (data.upgrade_threshold.met) return 'green'
  if (data.backfill?.is_running || data.upgrade_threshold.current > 0) return 'yellow'
  return 'red'
}

const STATUS_COLORS: Record<StatusLight, string> = {
  green: '#4ade80',
  yellow: '#fbbf24',
  neutral: '#8b949e',
  red: '#f87171',
}

export default function TrainingStatusCard() {
  const [data, setData] = useState<TrainingStatusData | null>(null)
  const [availability, setAvailability] = useState<AvailabilityState>({ kind: 'loading' })

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch('/api/training-status', {
        signal: AbortSignal.timeout(10_000),
      })
      if (!resp.ok) {
        if (resp.status === 404) {
          setData(null)
          setAvailability({ kind: 'not_enabled', diagnostic: 'HTTP 404' })
          return
        }
        setAvailability({
          kind: 'error',
          message: `HTTP ${resp.status}`,
          diagnostic: `HTTP ${resp.status}`,
        })
        return
      }
      const envelope = await resp.json()
      if (envelope.ok && envelope.data) {
        setData(envelope.data as TrainingStatusData)
        setAvailability({ kind: 'ready' })
      } else {
        const message = envelope.error?.message ?? 'Unknown error'
        setAvailability({
          kind: 'error',
          message,
          diagnostic: envelope.error?.code ? `${envelope.error.code}: ${message}` : message,
        })
      }
    } catch (err) {
      const diagnostic =
        err && typeof err === 'object' && 'message' in err && typeof err.message === 'string'
          ? err.message
          : 'Fetch failed'
      setAvailability({ kind: 'unavailable', diagnostic })
    }
  }, [])

  useEffect(() => {
    void fetchStatus()
    const interval = setInterval(() => void fetchStatus(), 30_000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  const statusLight = availability.kind === 'error' ? 'red' : getStatusLight(data)
  const lightColor = STATUS_COLORS[statusLight]
  const neutralMessage =
    availability.kind === 'not_enabled'
      ? '訓練資料未啟用'
      : availability.kind === 'unavailable'
        ? '訓練狀態暫不可用'
        : null
  const diagnostic =
    availability.kind === 'not_enabled' || availability.kind === 'unavailable' || availability.kind === 'error'
      ? availability.diagnostic
      : undefined

  return (
    <div
      className="hermes-clip"
      style={{
        background: 'var(--color-hermes-card, #161b22)',
        border: '1px solid var(--color-hermes-bd, #30363d)',
        borderRadius: 8,
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          aria-label={`Status: ${statusLight === 'green' ? '已達標' : statusLight === 'yellow' ? '進行中' : statusLight === 'neutral' ? '中性' : '停止'}`}
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: lightColor,
            boxShadow: `0 0 6px ${lightColor}`,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 10,
            letterSpacing: '1.2px',
            color: 'var(--color-hermes-tx3, #8b949e)',
            textTransform: 'uppercase',
            fontWeight: 600,
          }}
        >
          訓練資料
        </span>
      </div>

      {neutralMessage && (
        <div
          data-training-status-diagnostic={diagnostic}
          style={{ fontSize: 10.5, color: 'var(--color-hermes-tx3, #8b949e)' }}
          title={diagnostic}
        >
          {neutralMessage}
        </div>
      )}

      {availability.kind === 'error' && (
        <div
          data-training-status-diagnostic={diagnostic}
          style={{ fontSize: 10.5, color: '#f87171' }}
          title={diagnostic}
        >
          ⚠ {availability.message}
        </div>
      )}

      {data && (
        <>
          {/* Progress bar: current / target */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--color-hermes-tx2, #c9d1d9)', marginBottom: 4 }}>
              <span>方向標註進度</span>
              <span style={{ fontWeight: 600, color: lightColor }}>
                {data.upgrade_threshold.current} / {data.upgrade_threshold.target}
              </span>
            </div>
            <div
              style={{
                height: 6,
                width: '100%',
                background: 'var(--color-hermes-inset, #0d1117)',
                borderRadius: 3,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  height: '100%',
                  width: `${Math.min(100, data.upgrade_threshold.pct)}%`,
                  background: lightColor,
                  borderRadius: 3,
                  transition: 'width 0.3s ease',
                }}
              />
            </div>
            <div style={{ fontSize: 9.5, color: 'var(--color-hermes-tx3, #8b949e)', marginTop: 3 }}>
              {data.upgrade_threshold.met ? '✓ 已達升級門檻' : `差 ${data.upgrade_threshold.target - data.upgrade_threshold.current} 筆達標`}
            </div>
          </div>

          {/* Statistics */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5 }}>
              <span style={{ color: 'var(--color-hermes-tx3, #8b949e)' }}>總筆數</span>
              <span style={{ color: 'var(--color-hermes-tx, #e6edf3)', fontWeight: 600 }}>{data.training_data.total_records}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5 }}>
              <span style={{ color: 'var(--color-hermes-tx3, #8b949e)' }}>有方向比例</span>
              <span style={{ color: 'var(--color-hermes-tx, #e6edf3)', fontWeight: 600 }}>{(data.training_data.direction_ratio * 100).toFixed(1)}%</span>
            </div>
            {data.backfill && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5 }}>
                <span style={{ color: 'var(--color-hermes-tx3, #8b949e)' }}>回填狀態</span>
                <span style={{ color: data.backfill.is_running ? '#4ade80' : 'var(--color-hermes-tx2, #c9d1d9)', fontWeight: 600 }}>
                  {data.backfill.is_running ? `進行中 ${data.backfill.progress_pct}%` : `完成 ${data.backfill.completed}/${data.backfill.total}`}
                </span>
              </div>
            )}
          </div>

          {/* Per coin mini grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 4 }}>
            {Object.entries(data.training_data.per_coin).map(([coin, stat]) => (
              <div
                key={coin}
                title={`${coin}: ${stat.has_direction}/${stat.total} 有方向`}
                style={{
                  background: 'var(--color-hermes-inset, #0d1117)',
                  borderRadius: 4,
                  padding: '4px 2px',
                  textAlign: 'center',
                  fontSize: 9,
                  color: 'var(--color-hermes-tx2, #c9d1d9)',
                }}
              >
                <div style={{ fontWeight: 700 }}>{coin}</div>
                <div>{stat.total}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {!data && availability.kind === 'loading' && (
        <div style={{ fontSize: 10.5, color: 'var(--color-hermes-tx3, #8b949e)' }}>
          載入中…
        </div>
      )}
    </div>
  )
}
