/**
 * MultiAngleOverview — 五角度綜合分析總覽元件 (#810).
 *
 * Desktop: table layout
 * Mobile: card layout (via CSS grid)
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useHermesI18n } from './hermesI18n'
import ConflictBadge from './ConflictBadge'
import type { AngleResult, MultiAngleReport } from '../lib/multiAngleEndpoints'
import { fetchMultiAngleReport, submitMultiAngle } from '../lib/multiAngleEndpoints'
import { getAnalysisJob } from '../lib/endpoints'
import AnalysisReportView from '../components/AnalysisReportView'
import type { AnalyzeData } from '../lib/types'

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
  const [expanded, setExpanded] = useState<{ jobId: string; data: AnalyzeData } | null>(null)
  const generation = useRef(0)
  const timers = useRef(new Set<number>())
  const controller = useRef(new AbortController())

  const schedule = useCallback((callback: () => void, delay: number) => {
    const timer = window.setTimeout(() => {
      timers.current.delete(timer)
      callback()
    }, delay)
    timers.current.add(timer)
  }, [])

  useEffect(() => {
    generation.current += 1
    controller.current.abort()
    controller.current = new AbortController()
    const activeController = controller.current
    const activeTimers = timers.current
    setExpanded(null)
    return () => {
      generation.current += 1
      activeController.abort()
      activeTimers.forEach(window.clearTimeout)
      activeTimers.clear()
    }
  }, [coin, snapshotId])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchMultiAngleReport(coin, snapshotId, controller.current.signal)
      setReport(result.multi_angle)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '載入失敗')
    } finally {
      setLoading(false)
    }
  }, [coin, snapshotId])

  useEffect(() => { load() }, [load])

  const handleSubmit = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    try {
      const result = await submitMultiAngle(coin, undefined, locale, controller.current.signal)
      const requestGeneration = generation.current
      const deadline = Date.now() + 10 * 60 * 1000
      let transientFailures = 0
      const poll = async () => {
        if (requestGeneration !== generation.current) return
        const states = await Promise.all(
          Object.values(result.job_ids).map((jobId) => getAnalysisJob(jobId, controller.current.signal)),
        )
        if (states.some((state) => !state.ok)) {
          transientFailures += 1
          if (transientFailures > 5) throw new Error('分析狀態暫時無法讀取')
          schedule(() => { void poll().catch(handlePollError) }, 1500)
          return
        }
        transientFailures = 0
        const failed = states.find((state) => state.ok && state.data.state === 'failed')
        if (failed?.ok) {
          throw new Error(failed.data.error || '其中一個分析角度執行失敗')
        }
        if (states.every((state) => state.ok && state.data.state === 'completed')) {
          const data = await fetchMultiAngleReport(coin, result.snapshot_id, controller.current.signal)
          if (data.multi_angle) {
            setReport(data.multi_angle)
            setSubmitting(false)
            return
          }
        }
        if (Date.now() >= deadline) {
          throw new Error('分析工作長時間沒有完成，請稍後再確認')
        }
        schedule(() => { void poll().catch(handlePollError) }, 1500)
      }
      const handlePollError = (reason: unknown) => {
        setError(reason instanceof Error ? reason.message : '分析狀態讀取失敗')
        setSubmitting(false)
      }
      schedule(() => { void poll().catch(handlePollError) }, 1000)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提交失敗')
      setSubmitting(false)
    }
  }, [coin, locale, schedule])

  const handleAngleClick = useCallback(async (angle: AngleResult) => {
    onAngleClick?.(angle)
    if (!angle.job_id) return
    if (expanded?.jobId === angle.job_id) {
      setExpanded(null)
      return
    }
    const requestGeneration = generation.current
    const result = await getAnalysisJob(angle.job_id, controller.current.signal)
    if (requestGeneration !== generation.current) return
    if (!result.ok || result.data.state !== 'completed' || !result.data.result) {
      setError('此角度明細尚未完成')
      return
    }
    setExpanded({ jobId: angle.job_id, data: result.data.result })
  }, [expanded?.jobId, onAngleClick])

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
    '分歧': '⚡ 分歧', '不明': '🚫 無法判定',
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
        {report.decision_state !== 'normal' && (
          <span className="text-sm text-orange-400">
            {report.decision_state === 'partial_abstain' ? '部分角度棄權' : '全部角度棄權'}
          </span>
        )}
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
                onClick={() => { void handleAngleClick(angle) }}
                role="button"
                tabIndex={0}
                aria-label={`${modeLabels[angle.angle] ?? angle.angle} 詳細`}
                onKeyDown={(e) => e.key === 'Enter' && void handleAngleClick(angle)}
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
            onClick={() => { void handleAngleClick(angle) }}
            role="button"
            tabIndex={0}
            aria-label={`${modeLabels[angle.angle] ?? angle.angle} 詳細`}
            onKeyDown={(e) => e.key === 'Enter' && void handleAngleClick(angle)}
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
      {expanded && (
        <div className="mt-4 border-t border-gray-700 pt-4">
          <AnalysisReportView data={expanded.data} />
        </div>
      )}
    </section>
  )
}
