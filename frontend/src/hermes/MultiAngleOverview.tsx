/**
 * MultiAngleOverview — 五角度綜合分析總覽元件 (#810).
 *
 * Desktop: table layout
 * Mobile: card layout (via CSS grid)
 */
import { useCallback, useEffect, useState } from 'react'
import { useHermesI18n } from './hermesI18n'
import ConflictBadge from './ConflictBadge'
import type { AngleResult, MultiAngleReport } from '../lib/multiAngleEndpoints'
import { fetchMultiAngleReport, submitMultiAngle } from '../lib/multiAngleEndpoints'

const MODE_LABELS_ZH: Record<string, string> = {
  risk: '風險評估',
  sentiment: '市場情緒',
  fundamentals: '基本面',
  news: '新聞驗證',
  catalyst: '催化因素',
}

const MODE_LABELS_EN: Record<string, string> = {
  risk: 'Risk',
  sentiment: 'Sentiment',
  fundamentals: 'Fundamentals',
  news: 'News',
  catalyst: 'Catalyst',
}

const STATE_COLORS: Record<string, string> = {
  normal: '#22c55e',
  low_confidence: '#eab308',
  abstain: '#6b7280',
}

interface MultiAngleOverviewProps {
  coin: string
  snapshotId?: string
  onAngleClick?: (angle: AngleResult) => void
}

export default function MultiAngleOverview({ coin, snapshotId, onAngleClick }: MultiAngleOverviewProps) {
  const { t, locale } = useHermesI18n()
  const modeLabels = locale === 'en' ? MODE_LABELS_EN : MODE_LABELS_ZH
  const [report, setReport] = useState<MultiAngleReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchMultiAngleReport(coin, snapshotId)
      setReport(result?.multi_angle ?? null)
    } catch {
      setError('載入失敗')
    } finally {
      setLoading(false)
    }
  }, [coin, snapshotId])

  useEffect(() => { load() }, [load])

  const handleSubmit = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    try {
      const result = await submitMultiAngle(coin)
      if (result) {
        // Poll until synthesis is ready (simple retry with backoff)
        let attempts = 0
        const poll = async () => {
          attempts++
          const data = await fetchMultiAngleReport(coin, result.snapshot_id)
          if (data?.multi_angle) {
            setReport(data.multi_angle)
            setSubmitting(false)
          } else if (attempts < 30) {
            setTimeout(poll, Math.min(2000 + attempts * 500, 10000))
          } else {
            setError('分析超時，請稍後重新整理')
            setSubmitting(false)
          }
        }
        setTimeout(poll, 3000)
      }
    } catch {
      setError('提交失敗')
      setSubmitting(false)
    }
  }, [coin])

  if (loading) {
    return <div className="animate-pulse text-center py-8 text-gray-400">{t('maTitle') || '五角度綜合分析'}...</div>
  }

  if (!report) {
    return (
      <div className="text-center py-8">
        <p className="text-gray-400 mb-4">{t('maNoResult') || '尚無五角度綜合分析結果'}</p>
        <button
          onClick={handleSubmit}
          disabled={submitting}
          className="px-4 py-2 rounded-lg font-medium transition-colors"
          style={{ backgroundColor: submitting ? '#374151' : '#7c3aed', color: '#fff' }}
          aria-label={t('maSubmit') || '執行五角度綜合分析'}
        >
          {submitting ? '⏳ 分析中...' : `⚡ ${t('maSubmit') || '執行五角度綜合分析'}`}
        </button>
        <p className="text-xs text-gray-500 mt-2">{t('maCostWarning') || '消耗約 5× 分析預算'}</p>
        {error && <p className="text-red-400 mt-2 text-sm">{error}</p>}
      </div>
    )
  }

  const consensusLabel = {
    '偏多': '📈 偏多', '偏空': '📉 偏空', '中性': '⚖️ 中性',
    '分歧': '⚡ 分歧', 'partial_abstain': '⚠️ 部分棄權', 'full_abstain': '🚫 全部棄權',
  }[report.consensus] ?? report.consensus

  return (
    <section aria-label={t('maTitle') || '五角度綜合分析'} className="rounded-xl border border-gray-700 p-4">
      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-4">
        <h3 className="text-lg font-bold">{coin} {t('maTitle') || '五角度綜合分析'}</h3>
        <span className="text-xs text-gray-400 font-mono">snapshot: {report.snapshot_id.slice(0, 20)}...</span>
      </div>

      {/* Consensus bar */}
      <div className="flex flex-wrap items-center gap-4 mb-4 p-3 rounded-lg" style={{ backgroundColor: 'rgba(124, 58, 237, 0.1)' }}>
        <span className="text-base font-semibold">{consensusLabel}</span>
        <span className="text-sm text-gray-300">
          {t('maConfidence') || '信心'} {report.consensus_confidence.toFixed(2)}
        </span>
        <span className="text-sm text-gray-300">
          {t('maIndependence') || '獨立性'} {(report.evidence_independence * 100).toFixed(0)}%
        </span>
        {report.conflicts.length > 0 && (
          <span className="text-sm text-orange-400">
            {report.conflicts.length} {t('maConflict') || '分歧'}
          </span>
        )}
      </div>

      {/* Angle table (desktop) / cards (mobile) */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-400 border-b border-gray-700">
              <th className="py-2 px-2">{t('maAngle') || '角度'}</th>
              <th className="py-2 px-2">{t('maDirection') || '結論'}</th>
              <th className="py-2 px-2">{t('maConfidence') || '信心'}</th>
              <th className="py-2 px-2">{t('maState') || '狀態'}</th>
              <th className="py-2 px-2">{t('maConflict') || '分歧'}</th>
            </tr>
          </thead>
          <tbody>
            {report.angles.map((angle) => (
              <tr
                key={angle.angle}
                className="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer transition-colors"
                onClick={() => onAngleClick?.(angle)}
                role="button"
                tabIndex={0}
                aria-label={`${modeLabels[angle.angle] ?? angle.angle} 詳細`}
                onKeyDown={(e) => e.key === 'Enter' && onAngleClick?.(angle)}
              >
                <td className="py-2 px-2 font-medium">{modeLabels[angle.angle] ?? angle.angle}</td>
                <td className="py-2 px-2">{angle.decision_state === 'abstain' ? '—' : angle.direction}</td>
                <td className="py-2 px-2">
                  {angle.decision_state === 'abstain' ? '—' : angle.calibrated_confidence.toFixed(2)}
                </td>
                <td className="py-2 px-2">
                  <span
                    className="inline-block w-2 h-2 rounded-full mr-1"
                    style={{ backgroundColor: STATE_COLORS[angle.decision_state] ?? '#6b7280' }}
                  />
                  {angle.decision_state}
                </td>
                <td className="py-2 px-2">
                  <ConflictBadge conflicts={report.conflicts} currentAngle={angle.angle} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile cards */}
      <div className="md:hidden grid gap-3">
        {report.angles.map((angle) => (
          <div
            key={angle.angle}
            className="rounded-lg border border-gray-700 p-3 cursor-pointer hover:border-purple-500 transition-colors"
            onClick={() => onAngleClick?.(angle)}
            role="button"
            tabIndex={0}
            aria-label={`${modeLabels[angle.angle] ?? angle.angle} 詳細`}
            onKeyDown={(e) => e.key === 'Enter' && onAngleClick?.(angle)}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium">{modeLabels[angle.angle] ?? angle.angle}</span>
              <span
                className="text-xs px-2 py-0.5 rounded-full"
                style={{ backgroundColor: STATE_COLORS[angle.decision_state] ?? '#6b7280', color: '#fff' }}
              >
                {angle.decision_state === 'abstain' ? '棄權' : angle.direction}
              </span>
            </div>
            {angle.decision_state !== 'abstain' && (
              <div className="text-xs text-gray-400">
                信心 {angle.calibrated_confidence.toFixed(2)}
              </div>
            )}
            <ConflictBadge conflicts={report.conflicts} currentAngle={angle.angle} />
          </div>
        ))}
      </div>

      {/* Limits */}
      {report.limits.length > 0 && (
        <div className="mt-4 text-xs text-gray-400">
          {report.limits.map((lim, i) => (
            <p key={i} className="mb-1">⚠️ {lim}</p>
          ))}
        </div>
      )}

      {/* Synthesis summary */}
      <div className="mt-3 text-sm text-gray-300 italic">
        {report.narration ?? report.synthesis_summary}
      </div>
    </section>
  )
}
