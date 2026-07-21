import { TIER_COLOR, type GalaxyModel } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'
import type { ServiceMonitorState } from '../pages/HermesDashboard'
import type { AnalysisQuestionContext } from '../lib/endpoints'
import { BEGINNER_INTENTS, type AnalysisModeId } from '../lib/beginnerExperience'
import GlossaryTerm from '../components/GlossaryTerm'
import { latestUniqueConversationMessages } from '../lib/conversationMessages'

interface HermesLeftRailProps {
  model: GalaxyModel
  uplinkLatency?: string
  hermesMessage: string
  hasOrder: boolean
  qtype: string
  qtypes: string[]
  query: string
  submitLabel: string
  disabled?: boolean
  serviceMonitor?: Record<string, ServiceMonitorState>
  questionContext?: AnalysisQuestionContext | null
  onRecallQuestion?: (question: string) => void
  onType: (v: string) => void
  onQuery: (v: string) => void
  onSubmit: () => void
  beginnerMode?: boolean
  recommendedMode?: AnalysisModeId
  onChooseIntent?: (mode: AnalysisModeId, question: string) => void
  onApplyRecommendedMode?: (mode: AnalysisModeId) => void
}

export default function HermesLeftRail({
  model, uplinkLatency = '2.4s', hermesMessage, hasOrder, qtype, qtypes, query, submitLabel,
  onType, onQuery, onSubmit, disabled = false, serviceMonitor = {},
  questionContext = null, onRecallQuestion,
  beginnerMode = false, recommendedMode = 'risk', onChooseIntent, onApplyRecommendedMode,
}: HermesLeftRailProps) {
  const { t } = useHermesI18n()
  const { tierCounts, coins } = model
  const visibleConversation = latestUniqueConversationMessages(questionContext?.conversation ?? [])
  return (
    <div
      className="hermes-glass"
      data-region="left-rail"
      style={{
        position: 'absolute', left: 0, top: 'var(--hermes-top)', width: 'var(--hermes-rail)', height: 'calc(100% - var(--hermes-top) - var(--hermes-bottom))', zIndex: 5,
        borderRight: '1px solid var(--color-hermes-bd)', padding: '14px 16px',
        display: 'flex', flexDirection: 'column', gap: 12,
        overflowY: 'auto',
      }}
    >
      {!beginnerMode && <div>
        <div style={{ fontSize: 10, letterSpacing: '1.6px', color: 'var(--color-hermes-tx3)', marginBottom: 9 }}>{t('telemetry')}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>{t('tracked')}</span><span style={{ color: 'var(--color-hermes-tx)' }}>{coins.length}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>{t('healthy')}</span><span style={{ color: TIER_COLOR.healthy }}>{tierCounts.healthy}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>{t('moderate')}</span><span style={{ color: TIER_COLOR.moderate }}>{tierCounts.moderate}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>{t('danger')}</span><span style={{ color: TIER_COLOR.danger }}>{tierCounts.danger}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>{t('latency')}</span><span style={{ color: 'var(--color-hermes-tx)' }}>{uplinkLatency}</span></div>
          <div className="hermes-service-monitor" aria-label="system link monitor">
            {Object.entries(serviceMonitor).map(([name, state]) => {
              const label = state === 'ok' ? 'UP' : state === 'empty' ? 'NO DATA' : state === 'stale' ? 'DEGRADED' : state === 'error' ? 'DOWN' : 'CHECK'
              return <span key={name} className={`is-${state}`} title={`${name}: ${label}`}><i />{name} · {label}</span>
            })}
          </div>
        </div>
      </div>}

      {/* HERMES CONSOLE */}
      <div
        className="hermes-clip"
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', background: 'rgba(13,20,30,.6)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, boxShadow: 'inset 0 0 24px rgba(77,216,224,.04)' }}
      >
        {beginnerMode && (
          <div className="hermes-intent-picker">
            <div className="hermes-intent-title">我想做什麼？</div>
            <p>選一個目的，Hermes 會替你準備問題與分析方式。</p>
            <div>
              {BEGINNER_INTENTS.map((intent) => (
                <button key={intent.id} type="button" onClick={() => onChooseIntent?.(intent.mode, intent.question)} title={intent.description}>
                  <b>{intent.label}</b><span>{intent.description}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <div style={{ position: 'relative', width: 24, height: 24, flexShrink: 0, animation: 'hermes-hermes-breathe 3.2s ease-in-out infinite' }}>
            <div style={{ position: 'absolute', inset: 0, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', border: '1.5px solid var(--color-hermes-amber)', animation: 'hermes-orbit-spin 9s linear infinite' }} />
            <div style={{ position: 'absolute', inset: 5, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', background: 'var(--color-hermes-amber)', opacity: 0.35, animation: 'hermes-orbit-spin-rev 5s linear infinite' }} />
          </div>
          <span style={{ fontSize: 11, letterSpacing: '1.2px', color: 'var(--color-hermes-tx)' }}>HERMES</span>
          <span style={{ fontSize: 9, color: 'var(--color-hermes-cyan)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 3, padding: '1px 6px' }}>{t('online')}</span>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          <div style={{ minHeight: 82, flexShrink: 0, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: '2px solid var(--color-hermes-amber)', borderRadius: '0 6px 6px 0', padding: '9px 11px', overflow: 'hidden' }}>
            <div style={{ fontSize: 9, color: 'var(--color-hermes-amber)', letterSpacing: 1, marginBottom: 4 }}>{t('agentOutput')}</div>
            <div aria-label={t('agentOutput')} style={{ maxHeight: 62, overflowY: 'auto', fontSize: 11.5, lineHeight: 1.5, color: 'var(--color-hermes-tx)', overflowWrap: 'anywhere' }}>{hermesMessage}</div>
          </div>
          {(visibleConversation.length || hasOrder) ? (
            <div style={{ background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 6, padding: '8px 11px', alignSelf: 'flex-end', maxWidth: '92%' }}>
              <div style={{ fontSize: 9, color: 'var(--color-hermes-tx3)', letterSpacing: 1, marginBottom: 3 }}>對話記憶 · SQLITE</div>
              {visibleConversation.map((message) => (
                <div key={message.message_id} style={{ fontSize: 10.5, lineHeight: 1.4, color: message.role === 'hermes' ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-tx2)', marginTop: 4 }}>
                  {message.role === 'hermes' ? 'HERMES' : 'YOU'} › {message.content}
                </div>
              ))}
              {!visibleConversation.length && <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--color-hermes-tx2)' }}>&gt; {qtype}: {query}</div>}
            </div>
          ) : null}
          {!!questionContext?.matches.length && (
            <div style={{ borderTop: '1px solid var(--color-hermes-bd)', paddingTop: 7 }}>
              <div style={{ fontSize: 9, letterSpacing: 1, color: 'var(--color-hermes-cyan)', marginBottom: 2 }}><GlossaryTerm term="rag" label="相似歷史題目" compact /></div>
              <div role="note" style={{ fontSize: 8.5, lineHeight: 1.35, color: 'var(--color-hermes-amber)', marginBottom: 5 }}>
                歷史參考 · 非本次 Evidence，不參與信任評分
              </div>
              {questionContext.matches.slice(0, 3).map((match) => (
                <button key={match.question_id} type="button" onClick={() => onRecallQuestion?.(match.question)} title={match.answer ?? '尚無完成快照'}
                  style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 0, borderBottom: '1px solid var(--color-hermes-bd)', color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 10, lineHeight: 1.35, padding: '5px 2px', cursor: 'pointer' }}>
                  <b style={{ color: 'var(--color-hermes-amber)' }}>{Math.round(match.similarity * 100)}%</b> · {match.coin}/{match.mode} · {match.question}
                </button>
              ))}
            </div>
          )}
        </div>

        <label style={{ display: 'block', fontSize: 10, color: 'var(--color-hermes-tx2)', marginBottom: 5 }}>{t('analysisMode')}</label>
        <div style={{ position: 'relative', marginBottom: 10 }}>
          <select
            value={qtype}
            onChange={(e) => onType(e.target.value)}
            style={{ width: '100%', appearance: 'none', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 12, padding: '8px 10px', cursor: 'pointer' }}
          >
            {qtypes.map((q) => <option key={q} value={q}>{q}</option>)}
          </select>
          <span style={{ position: 'absolute', right: 10, top: 10, color: 'var(--color-hermes-tx3)', pointerEvents: 'none', fontSize: 10 }}>▼</span>
        </div>

        <label style={{ display: 'block', fontSize: 10, color: 'var(--color-hermes-tx2)', marginBottom: 5 }}>{t('order')}</label>
        <textarea
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          rows={2}
          style={{ width: '100%', resize: 'none', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 11.5, lineHeight: 1.5, padding: '8px 10px', marginBottom: 10 }}
        />

        {beginnerMode && qtype !== qtypes[['risk', 'sentiment', 'fundamentals', 'news', 'catalyst'].indexOf(recommendedMode)] && (
          <button type="button" className="hermes-mode-suggestion" onClick={() => onApplyRecommendedMode?.(recommendedMode)}>
            建議改用「{qtypes[['risk', 'sentiment', 'fundamentals', 'news', 'catalyst'].indexOf(recommendedMode)]}」分析
          </button>
        )}

        {beginnerMode && <div className="hermes-analysis-expectation">將使用目前可用的多來源資料進行「{qtype}」，整理可信程度、主要原因與不確定性。</div>}

        <button
          onClick={onSubmit}
          disabled={disabled}
          style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: 'var(--color-hermes-amber)', border: 'none', borderRadius: 5, color: '#1a1206', fontWeight: 700, fontSize: 12, padding: 9, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .55 : 1, transition: 'filter .15s, transform .08s' }}
          onMouseEnter={(e) => (e.currentTarget.style.filter = 'brightness(1.12)')}
          onMouseLeave={(e) => (e.currentTarget.style.filter = 'none')}
          onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(.96)')}
          onMouseUp={(e) => (e.currentTarget.style.transform = 'none')}
        >
          <span>{submitLabel}</span><span>⤴</span>
        </button>
      </div>
    </div>
  )
}
