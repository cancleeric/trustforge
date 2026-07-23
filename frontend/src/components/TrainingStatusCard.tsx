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

type StatusLight = 'green' | 'yellow' | 'red'

type StatusProblem =
  | { kind: 'optional-unavailable'; message: string; diagnostic: string }
  | { kind: 'temporary-unavailable'; message: string; diagnostic: string }
  | { kind: 'error'; message: string }

function getStatusLight(data: TrainingStatusData | null, problem: StatusProblem | null): StatusLight {
  if (problem?.kind === 'optional-unavailable' || problem?.kind === 'temporary-unavailable') return 'yellow'
  if (problem?.kind === 'error') return 'red'
  if (!data) return 'red'
  if (data.upgrade_threshold.met) return 'green'
  if (data.backfill?.is_running || data.upgrade_threshold.current > 0) return 'yellow'
  return 'red'
}

const STATUS_COLORS: Record<StatusLight, string> = {
  green: '#4ade80',
  yellow: '#fbbf24',
  red: '#f87171',
}

export default function TrainingStatusCard() {
  const [data, setData] = useState<TrainingStatusData | null>(null)
  const [problem, setProblem] = useState<StatusProblem | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch('/api/training-status', {
        signal: AbortSignal.timeout(10_000),
      })
      if (!resp.ok) {
        if (resp.status === 404) {
          setData(null)
          setProblem({
            kind: 'optional-unavailable',
            message: '訓練資料未啟用',
            diagnostic: `training-status endpoint returned ${resp.status}`,
          })
          return
        }
        setProblem({ kind: 'error', message: `HTTP ${resp.status}` })
        return
      }
      const envelope = await resp.json()
      if (envelope.ok && envelope.data) {
        setData(envelope.data as TrainingStatusData)
        setProblem(null)
      } else {
        setProblem({ kind: 'error', message: envelope.error?.message ?? 'Unknown error' })
      }
    } catch (err) {
      setProblem({
        kind: 'temporary-unavailable',
        message: '訓練狀態暫不可用',
        diagnostic: err instanceof Error ? err.message : 'Fetch failed',
      })
    }
  }, [])

  useEffect(() => {
    void fetchStatus()
    const interval = setInterval(() => void fetchStatus(), 30_000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  const statusLight = getStatusLight(data, problem)
  const lightColor = STATUS_COLORS[statusLight]
  const isNeutralProblem = problem?.kind === 'optional-unavailable' || problem?.kind === 'temporary-unavailable'

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
          aria-label={`Status: ${statusLight === 'green' ? '已達標' : statusLight === 'yellow' ? '進行中' : '停止'}`}
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

      {problem && (
        <div
          data-diagnostic={problem.kind === 'error' ? undefined : problem.diagnostic}
          style={{
            fontSize: 10.5,
            color: isNeutralProblem ? 'var(--color-hermes-tx2, #c9d1d9)' : '#f87171',
          }}
        >
          {isNeutralProblem ? problem.message : `⚠ ${problem.message}`}
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

      {!data && !problem && (
        <div style={{ fontSize: 10.5, color: 'var(--color-hermes-tx3, #8b949e)' }}>
          載入中…
        </div>
      )}
    </div>
  )
}
