import { useState } from 'react'
import { COMPONENT_WEIGHTS, HERMES_CYAN, HERMES_AMBER, HERMES_RED, STAGE_DEFS, type GalaxyCoin, type SelectedDerivation } from '../lib/hermesData'
import { modeLabel, useHermesI18n } from './hermesI18n'
import { requeueAnalysis, type AnalysisFlowData, type AnalysisJourneyData } from '../lib/endpoints'
import type { BridgeHologramData } from '../components/BridgeHologramContext'
import EvidenceTable from '../components/EvidenceTable'
import { groupByStance } from '../lib/stancePairs'
import type { MessageKey } from './hermesI18n'

/** N72：五關各自的「這關在做什麼／下面的數字怎麼看」。用查表而不是樣板字串
 *  組 key——`selectedStage` 型別是 string，樣板組出來的 key 型別檢查擋不住
 *  拼錯，查表則是查不到就不渲染（divergence 這種沒有對應說明的也安全）。 */
const STAGE_ABOUT: Record<string, { about: MessageKey; read: MessageKey }> = {
  scan: { about: 'stageAboutScan', read: 'stageReadScan' },
  filter: { about: 'stageAboutFilter', read: 'stageReadFilter' },
  crossverify: { about: 'stageAboutCrossverify', read: 'stageReadCrossverify' },
  manipulation: { about: 'stageAboutManipulation', read: 'stageReadManipulation' },
  composite: { about: 'stageAboutComposite', read: 'stageReadComposite' },
}

interface StageDrilldownProps {
  selCoin: GalaxyCoin
  derivation: SelectedDerivation
  selectedStage: string
  onClose: () => void
  flow?: AnalysisFlowData | null
  journey?: AnalysisJourneyData | null
  telemetry?: BridgeHologramData | null
}

export default function StageDrilldown({ selCoin, derivation: fallbackDerivation, selectedStage, onClose, flow, journey, telemetry }: StageDrilldownProps) {
  const { t } = useHermesI18n()
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const isDivergence = selectedStage === 'divergence'
  const selDef = !isDivergence ? STAGE_DEFS.find((s) => s.id === selectedStage) : null
  const label = isDivergence ? t('divergence') : selectedStage === 'scan' ? t('scan') : selectedStage === 'filter' ? t('filter') : selectedStage === 'crossverify' ? t('crossverify') : selectedStage === 'manipulation' ? t('manipulation') : t('composite')
  const icon = isDivergence ? '⚠' : selDef?.icon ?? ''
  const color = isDivergence ? HERMES_RED : selectedStage === 'manipulation' ? HERMES_AMBER
    : selectedStage === 'crossverify' ? fallbackDerivation.divColor
      : selectedStage === 'composite' ? (selCoin.tier === 'healthy' ? HERMES_CYAN : selCoin.tier === 'moderate' ? HERMES_AMBER : HERMES_RED)
        : HERMES_CYAN

  const toggle = (key: string) => setExpanded((e) => ({ ...e, [key]: !e[key] }))
  const componentLabel = (value: string) => value === 'Reputation' ? t('reputation') : value === 'Corroboration' ? t('corroboration') : value === 'Recency' ? t('recency') : t('resistance')
  const reasoningKind = (value: string) => value === 'FACTS' ? t('facts') : value === 'INFERENCE' ? t('inference') : t('conclusion')
  const stageIndex = Math.max(0, STAGE_DEFS.findIndex((stage) => stage.id === selectedStage))
  const liveStage = telemetry?.runId ? undefined : flow?.stages[stageIndex]
  const stageId = liveStage?.id
  const recentAttempts = journey?.jobs.flatMap((job) => job.coin === selCoin.name && stageId
    ? job.attempts.filter((attempt) => attempt.stage === stageId).map((attempt) => ({ ...attempt, question: job.question, snapshot: job.snapshot_id })) : []).slice(0, 8) ?? []
  const deadLetters = journey?.dead_letters.filter((item) => item.coin === selCoin.name && item.stage === stageId) ?? []
  const analysis = telemetry?.analysis
  const completedStage = telemetry?.pipelineStages?.[stageIndex]
  const evidence = analysis?.evidence ?? []
  const passedEvidence = evidence.filter((item) => item.flags.length === 0)
  const flaggedEvidence = evidence.filter((item) => item.flags.length > 0)
  const aggregate = analysis?.trust_components_aggregate
  const actualComponents = aggregate ? [
    { label: 'Reputation', value: aggregate.reputation, weight: COMPONENT_WEIGHTS[0] },
    { label: 'Corroboration', value: aggregate.corroboration, weight: COMPONENT_WEIGHTS[1] },
    { label: 'Recency', value: aggregate.recency, weight: COMPONENT_WEIGHTS[2] },
    { label: 'Manipulation resistance', value: aggregate.manipulation == null ? null : 1 - aggregate.manipulation, weight: COMPONENT_WEIGHTS[3] },
  ].filter((item): item is { label: string; value: number; weight: number } => item.value !== null)
    .map((item) => ({ label: item.label, score: Math.round(item.value * 100), weight: item.weight })) : []
  const report = analysis?.report
  const derivation = analysis ? {
    ...fallbackDerivation,
    scanned: evidence.length,
    passedCount: passedEvidence.length,
    flaggedCount: flaggedEvidence.length,
    scanItems: evidence.map((item) => ({
      name: item.source,
      time: new Date(item.fetched_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      credibility: Math.round(item.trust * 100),
      note: item.content_reference,
    })),
    passedItems: passedEvidence.map((item) => item.source),
    droppedItems: flaggedEvidence.map((item) => ({ name: item.source, reason: item.flags.join(', ') })),
    crossItems: (() => {
      const { bullish, bearish } = groupByStance(report?.cross_source_signal)
      return [
        ...bullish.map(item => ({ stance: item.stance, claim: item.text ?? '', source: item.source, color: HERMES_CYAN })),
        ...bearish.map(item => ({ stance: item.stance, claim: item.text ?? '', source: item.source, color: HERMES_AMBER })),
      ]
    })(),
    manipulationItems: flaggedEvidence.flatMap((item) => item.flags.map((flag) => `${item.source}: ${flag}`)),
    components: actualComponents.map((item) => ({ ...item, barColor: item.score >= 75 ? HERMES_CYAN : item.score >= 50 ? HERMES_AMBER : HERMES_RED })),
    steps: [
      ...(report?.facts ?? []).slice(0, 2).map((text) => ({ kind: 'FACTS', indent: 0, color: HERMES_CYAN, text })),
      ...(report?.inferences ?? []).slice(0, 2).map((text) => ({ kind: 'INFERENCE', indent: 22, color: HERMES_AMBER, text })),
      ...(report ? [{ kind: 'CONCLUSION', indent: 44, color: HERMES_CYAN, text: report.market_judgment }] : []),
    ],
  } : fallbackDerivation

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="hermes-stage-title"
      className="hermes-clip-lg hermes-stage-drilldown"
      style={{
        background: 'rgba(8,14,22,.92)', backdropFilter: 'blur(6px)', willChange: 'backdrop-filter', border: `1px solid ${color}`,
        borderRadius: 10, boxShadow: '0 20px 60px rgba(0,0,0,.5)', display: 'flex', flexDirection: 'column',
        animation: 'hermes-panel-in .22s ease-out',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--color-hermes-bd)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ fontSize: 15, color }}>{icon}</span>
          <div>
            <span id="hermes-stage-title" style={{ fontWeight: 700, fontSize: 13, color: 'var(--color-hermes-tx)' }}>{selCoin.full} — {label}</span>
            <div style={{ marginTop: 3, fontSize: 8.5, letterSpacing: '.8px', color: 'var(--color-hermes-amber)' }}>{t('proxyTrace')}</div>
          </div>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx2)', fontSize: 10, padding: '4px 9px', cursor: 'pointer' }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = color; e.currentTarget.style.color = 'var(--color-hermes-tx)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-hermes-bd2)'; e.currentTarget.style.color = 'var(--color-hermes-tx2)' }}
        >{t('close')} ✕</button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 18px' }}>
        {/* N72（CEO：「中間這幾個是幹嘛用的 要寫清楚」）：這五關（來源掃描／
            可信過濾／交叉驗證／操縱偵測／綜合信任分數）以前打開只看得到
            worker 代號、佇列數與一堆數值，從頭到尾沒有一句話說這一關在做
            什麼。第一句先用白話講「這關在幹嘛」，第二句講「所以下面這些
            數字要怎麼看」，再進技術細節。 */}
        {STAGE_ABOUT[selectedStage] && (
          <div style={{ marginBottom: 14, padding: '11px 12px', borderLeft: `2px solid ${color}`, background: 'rgba(4,10,17,.72)', borderRadius: 4, fontSize: 11.5, lineHeight: 1.75, color: 'var(--color-hermes-tx)' }}>
            <div style={{ fontSize: 9, letterSpacing: '1px', color: 'var(--color-hermes-tx2)', marginBottom: 4 }}>{t('stageWhatIsThis')}</div>
            <div>{t(STAGE_ABOUT[selectedStage].about)}</div>
            <div style={{ marginTop: 5, color: 'var(--color-hermes-tx2)' }}>{t(STAGE_ABOUT[selectedStage].read)}</div>
          </div>
        )}
        {(liveStage || completedStage) && (
          <div style={{ marginBottom: 14, padding: 12, border: '1px solid var(--color-hermes-bd2)', background: 'rgba(4,10,17,.96)', borderRadius: 6, fontSize: 11, lineHeight: 1.7 }}>
            <div style={{ color: HERMES_CYAN, fontWeight: 700 }}>HERMES WORKER · {liveStage?.id ?? completedStage?.id}</div>
            <div>狀態：{liveStage?.current ? t('processing') : completedStage?.status === 'completed' ? '本次執行已完成' : completedStage?.status === 'failed' ? '本次執行失敗' : t('standby')}　{t('queued')}：{liveStage?.queued ?? 0}</div>
            {liveStage?.next_retry_at && <div style={{ color: HERMES_AMBER }}>下次重試：{new Date(liveStage.next_retry_at * 1000).toLocaleTimeString()}</div>}
            {liveStage?.current ? <>
              <div>幣別：{liveStage.current.coin}　模式：{modeLabel(liveStage.current.mode, t)}</div>
              <div>題目：{liveStage.current.question}</div>
              <div>Snapshot：{liveStage.current.snapshot_id}</div>
              <div>開始：{new Date(liveStage.current.started_at * 1000).toLocaleTimeString()}　重試：{liveStage.current.retry_count}</div>
              {liveStage.current.error && <div style={{ color: HERMES_RED }}>錯誤：{liveStage.current.error}</div>}
            </> : completedStage?.status === 'completed'
              ? <div style={{ color: 'var(--color-hermes-tx3)' }}>run {telemetry?.runId} · {completedStage.metric} {completedStage.unit}</div>
              : <div style={{ color: 'var(--color-hermes-tx3)' }}>目前沒有執行工作。</div>}
          </div>
        )}
        {(recentAttempts.length > 0 || deadLetters.length > 0) && (
          <div style={{ marginBottom: 14, display: 'grid', gap: 7 }}>
            <div style={{ color: 'var(--color-hermes-tx2)', fontSize: 10, letterSpacing: 1 }}>失敗與重試歷史</div>
            {recentAttempts.map((attempt) => <div key={attempt.attempt_id} style={{ padding: 8, border: `1px solid ${HERMES_RED}`, fontSize: 10.5 }}>
              <b style={{ color: HERMES_RED }}>第 {attempt.attempt} 次失敗</b> · {attempt.duration_sec.toFixed(2)}s · {attempt.retryable ? '可重試' : '不可重試'}<br />
              {attempt.question}<br /><small>{attempt.snapshot} · {attempt.error}</small>
            </div>)}
            {deadLetters.map((item) => <div key={item.job_id} style={{ padding: 8, background: 'rgba(255,95,95,.12)', border: `1px solid ${HERMES_RED}` }}>
              <b style={{ color: HERMES_RED }}>DEAD LETTER · {item.attempts} attempts</b><br />{item.error}<br />
              <button type="button" onClick={() => void requeueAnalysis(item.job_id)} style={{ marginTop: 6, padding: '4px 8px', color: HERMES_CYAN, border: `1px solid ${HERMES_CYAN}`, background: 'transparent' }}>重新排程</button>
            </div>)}
          </div>
        )}
        {selectedStage === 'scan' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {analysis && <div style={{ fontSize: 10.5, color: 'var(--color-hermes-tx2)', marginBottom: 4 }}>本次報告保留的 {evidence.length} 筆可追溯證據；不代表來源蒐集階段的原始文件總數。</div>}
            {derivation.scanItems.map((it, i) => {
              const key = `${selCoin.id}_${i}`
              const open = !!expanded[key]
              const credColor = it.credibility >= 75 ? HERMES_CYAN : it.credibility >= 50 ? HERMES_AMBER : HERMES_RED
              return (
                <div key={key} onClick={() => toggle(key)} style={{ cursor: 'pointer', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '8px 11px' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-hermes-hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-hermes-inset)')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-hermes-tx)', flex: 1 }}>{it.name}</span>
                    <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)' }}>{it.time}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: credColor, width: 34, textAlign: 'right' }}>{it.credibility}</span>
                  </div>
                  {open && <div style={{ marginTop: 7, paddingTop: 7, borderTop: '1px solid var(--color-hermes-bd)', fontSize: 11, color: 'var(--color-hermes-tx2)', lineHeight: 1.5 }}>{it.note}</div>}
                </div>
              )
            })}
          </div>
        )}

        {selectedStage === 'filter' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div style={{ fontSize: 10, color: HERMES_CYAN, letterSpacing: 1, marginBottom: 7 }}>{analysis ? '無操縱旗標' : t('passed')} · {derivation.passedCount}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {derivation.passedItems.map((p) => <span key={p} style={{ fontSize: 11, color: 'var(--color-hermes-tx)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 5, padding: '5px 9px' }}>{p}</span>)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: HERMES_RED, letterSpacing: 1, marginBottom: 7 }}>{analysis ? '有操縱旗標' : t('dropped')} · {derivation.flaggedCount}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {derivation.droppedItems.map((d) => (
                  <div key={d.name} style={{ fontSize: 11.5, color: 'var(--color-hermes-tx)', background: 'rgba(255,95,95,.14)', border: '1px solid rgba(255,95,95,.45)', borderRadius: 6, padding: '7px 10px' }}>
                    <span style={{ fontWeight: 600 }}>{d.name}</span> — <span style={{ color: 'var(--color-hermes-tx2)' }}>{d.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {(selectedStage === 'crossverify' || isDivergence) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ fontSize: 10.5, color: derivation.divColor, background: derivation.divDim, border: `1px solid ${derivation.divBd}`, borderRadius: 5, padding: '4px 9px', width: 'fit-content' }}>
              {analysis ? report?.cross_source_signal?.summary ?? '本次沒有形成可報告的跨來源訊號' : `${t('divergenceUnit')} · Δ ${derivation.divergence}%`}
            </div>
            {derivation.crossItems.map((cv, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 5, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: `3px solid ${cv.color}`, borderRadius: '0 6px 6px 0', padding: '9px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: cv.color }}>{cv.stance}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)' }}>{cv.source}</span>
                </div>
                <span style={{ fontSize: 12, color: 'var(--color-hermes-tx)', lineHeight: 1.5 }}>{cv.claim}</span>
              </div>
            ))}
          </div>
        )}

        {selectedStage === 'manipulation' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11.5, color: 'var(--color-hermes-tx2)' }}>{t('flaggedChannel')}: <b style={{ color: 'var(--color-hermes-tx)' }}>{analysis ? [...new Set(flaggedEvidence.map((item) => item.source))].join(', ') || '本次無確定操縱旗標' : 'Social Sentiment Scanner'}</b></div>
            {derivation.manipulationItems.map((m, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 11.5, color: 'var(--color-hermes-tx)', background: 'rgba(255,95,95,.14)', border: '1px solid rgba(255,95,95,.45)', borderRadius: 6, padding: '8px 11px' }}>
                <span style={{ color: HERMES_RED }}>✕</span><span>{m}</span>
              </div>
            ))}
          </div>
        )}

        {selectedStage === 'composite' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {derivation.components.map((c) => (
              <div key={c.label} style={{ display: 'flex', flexDirection: 'column', gap: 4, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '9px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontWeight: 600, fontSize: 11.5, flex: 1, color: 'var(--color-hermes-tx)' }}>{componentLabel(c.label)}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--color-hermes-tx3)' }}>{t('weight')} {c.weight}%</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: c.barColor, width: 28, textAlign: 'right' }}>{c.score}</span>
                </div>
              </div>
            ))}
            {evidence.length > 0 ? (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 10, letterSpacing: 1, color: 'var(--color-hermes-tx3)', marginBottom: 6 }}>{t('scanned')} ({evidence.length})</div>
                <EvidenceTable evidence={evidence} evidenceGroups={report?.evidence_groups} />
              </div>
            ) : (
              <div style={{ fontSize: 10.5, color: 'var(--color-hermes-tx3)', fontStyle: 'italic', marginTop: 8 }}>
                {analysis ? `${t('scanned')} ${evidence.length}` : t('proxyTrace')}
              </div>
            )}
            <div style={{ fontSize: 10, letterSpacing: 1, color: 'var(--color-hermes-tx3)', marginTop: 6 }}>{t('reasoningTrace')}</div>
            {derivation.steps.map((stp, i) => (
              <div key={i} style={{ marginLeft: stp.indent, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: `3px solid ${stp.color}`, borderRadius: '0 6px 6px 0', padding: '9px 12px' }}>
                <div style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: 1, color: stp.color, marginBottom: 5 }}>{reasoningKind(stp.kind)}</div>
                <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--color-hermes-tx)' }}>{stp.text}</p>
              </div>
            ))}
            {analysis && (
              <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  onClick={() => {
                    const payload = JSON.stringify({
                      evidence: analysis.evidence ?? [],
                      trust_radar: analysis.trust_radar ?? {},
                      execution_log: analysis.execution_log ?? [],
                    }, null, 2)
                    const href = URL.createObjectURL(new Blob([payload], { type: 'application/json' }))
                    const a = document.createElement('a')
                    a.href = href
                    a.download = `${telemetry?.runId ?? 'breakdown'}-breakdown.json`
                    a.click()
                    URL.revokeObjectURL(href)
                  }}
                  style={{
                    background: 'var(--color-hermes-inset)',
                    border: '1px solid var(--color-hermes-bd)',
                    borderRadius: 5,
                    padding: '5px 12px',
                    fontSize: 10.5,
                    color: 'var(--color-hermes-tx2)',
                    cursor: 'pointer',
                  }}
                >
                  {'⬇'} JSON
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
