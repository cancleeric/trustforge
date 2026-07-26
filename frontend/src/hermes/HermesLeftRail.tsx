import { TIER_COLOR, type GalaxyModel } from '../lib/hermesData'
import { modeLabel, useHermesI18n } from './hermesI18n'
import type { ServiceMonitorState } from '../pages/HermesDashboard'
import type { AnalysisQuestionContext } from '../lib/endpoints'
import { BEGINNER_INTENTS, type AnalysisModeId } from '../lib/beginnerExperience'
import GlossaryTerm from '../components/GlossaryTerm'

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
        /* N37: `minHeight: 0` 明確允許這塊被壓到比內容還矮，而左軌高度是
           `calc(100% - top - bottom)`，視窗一矮就沒空間分。實測 1024x420 時
           這個 .hermes-clip clientHeight 只剩 28px（scrollHeight 222），
           使用者看不到 HERMES 主控台的任何內容。改成 200px 樓地板：左軌自己
           已經是 overflow-y:auto，空間不夠時讓左軌滾，而不是把面板壓扁。
           200 = 稽核腳本的可讀性門檻 min(scrollHeight, 200)。 */
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 200, overflowY: 'auto', background: 'rgba(13,20,30,.6)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, boxShadow: 'inset 0 0 24px rgba(77,216,224,.04)' }}
      >
        {beginnerMode && (
          <div className="hermes-intent-picker">
            <div className="hermes-intent-title">{t('whatToDo')}</div>
            <p>{t('chooseGoal')}</p>
            <div>
              {BEGINNER_INTENTS.map((intent) => (
                <button key={intent.id} type="button" onClick={() => onChooseIntent?.(intent.mode, intent.question)} title={t(intent.descriptionKey)}>
                  <b>{t(intent.labelKey)}</b><span>{t(intent.descriptionKey)}</span>
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

        {/* N37: 同上——這層的 minHeight:0 實測讓「Hermes 主動報告」對話區在
            561x700 只剩 46px（scrollHeight 697）、900x620 和 1024x420 直接
            剩 0px，511~555px 的報告內容一個字都看不到。樓地板取 110px＝
            下面那顆 agentOutput 卡的 minHeight 82 ＋ gap ＋ 標題行。 */}
        <div style={{ flex: 1, minHeight: 110, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          <div style={{ minHeight: 82, flexShrink: 0, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: '2px solid var(--color-hermes-amber)', borderRadius: '0 6px 6px 0', padding: '9px 11px', overflow: 'hidden' }}>
            <div style={{ fontSize: 9, color: 'var(--color-hermes-amber)', letterSpacing: 1, marginBottom: 4 }}>{t('agentOutput')}</div>
            {/* N39: 硬鎖 62px＝11.5px 字大約只露 3 行，但 HERMES 的回覆實測遠比
                這個高：en 561x700 scrollHeight 224、768x1024 121、1280x800 104。
                也就是使用者要在一個只有內容 28% 高的縫裡捲完整段分析結論——這是
                主控台最核心的一塊內容，不該是最窄的一塊。
                改成 min(34vh, 260px)：矮視窗跟著視窗縮（不會把左軌其他區塊擠掉），
                高視窗最多 260px＝約 13 行，一次讀完常見長度的回覆而不必捲。
                字級 11.5→12.5：這段是要「讀」的散文，不是 telemetry 數字。 */}
            <div aria-label={t('agentOutput')} style={{ maxHeight: 'min(34vh, 260px)', overflowY: 'auto', fontSize: 12.5, lineHeight: 1.6, color: 'var(--color-hermes-tx)', overflowWrap: 'anywhere' }}>{hermesMessage}</div>
          </div>
          {(questionContext?.conversation.length || hasOrder) ? (
            <div style={{ background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 6, padding: '8px 11px', alignSelf: 'flex-end', maxWidth: '92%' }}>
              <div style={{ fontSize: 9, color: 'var(--color-hermes-tx3)', letterSpacing: 1, marginBottom: 3 }}>{t('conversationMemory')}</div>
              {(questionContext?.conversation ?? [])
                .filter((msg, i, arr) => i === 0 || msg.role !== arr[i - 1].role || msg.content !== arr[i - 1].content)
                .slice(-3)
                .map((message) => (
                <div key={message.message_id} style={{ fontSize: 10.5, lineHeight: 1.4, color: message.role === 'hermes' ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-tx2)', marginTop: 4 }}>
                  {message.role === 'hermes' ? 'HERMES' : 'YOU'} › {message.content}
                </div>
              ))}
              {!questionContext?.conversation.length && <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--color-hermes-tx2)' }}>&gt; {qtype}: {query}</div>}
            </div>
          ) : null}
          {!!questionContext && (
            <div style={{ borderTop: '1px solid var(--color-hermes-bd)', paddingTop: 7 }}>
              <div style={{ fontSize: 9, letterSpacing: 1, color: 'var(--color-hermes-cyan)', marginBottom: 2 }}><GlossaryTerm term="rag" label={t('similarQuestions')} compact /></div>
              <div role="note" style={{ fontSize: 8.5, lineHeight: 1.35, color: 'var(--color-hermes-amber)', marginBottom: 5 }}>
                {t('historyDisclaimer')}
              </div>
              {questionContext.matches.length ? questionContext.matches.slice(0, 3).map((match) => (
                <button key={match.question_id} type="button" onClick={() => onRecallQuestion?.(match.question)} title={match.answer ?? '尚無完成快照'}
                  style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 0, borderBottom: '1px solid var(--color-hermes-bd)', color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 10, lineHeight: 1.35, padding: '5px 2px', cursor: 'pointer' }}>
                  <b style={{ color: 'var(--color-hermes-amber)' }}>{Math.round(match.similarity * 100)}%</b> · {match.coin}/{modeLabel(match.mode, t)} · {match.question}
                </button>
              )) : (
                <div style={{ fontSize: 10, lineHeight: 1.4, color: 'var(--color-hermes-tx3)', padding: '5px 2px' }}>{t('noSimilarQuestions')}</div>
              )}
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
          rows={3}
          /* N39: 這顆是整個主控台唯一的輸入口，卻是最容易被壓爛的一塊——左軌是
             flex column，textarea 沒有 `flex-shrink: 0`，空間一緊就被壓到比
             `rows` 還矮。實測 zh-TW 561x700 clientHeight 只剩 16px（scrollHeight
             51），一行字被切掉上下半截；1280x800 也只有 40/51。
             三件事一起改：
               flexShrink: 0  不再讓 flex 壓縮輸入框（左軌本身 overflow-y:auto，
                              空間不夠就讓軌道滾，跟 N37 同一個處理原則）
               minHeight: 74  rows=3 的實高（3×19.2 + padding 16 + border 2），
                              明確給樓地板，不依賴 `rows` 這個會被 flex 蓋掉的值
               resize: 'vertical'  原本是 'none'，使用者連手動拉大都不行；
                              要寫長一點的任務描述時這是唯一的出路
             字級 11.5→12.5：使用者在這裡打字，不是讀 telemetry。 */
          style={{ width: '100%', resize: 'vertical', flexShrink: 0, minHeight: 74, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 12.5, lineHeight: 1.6, padding: '8px 10px', marginBottom: 10 }}
        />

        {beginnerMode && qtype !== qtypes[['risk', 'sentiment', 'fundamentals', 'news', 'catalyst'].indexOf(recommendedMode)] && (
          <button type="button" className="hermes-mode-suggestion" onClick={() => onApplyRecommendedMode?.(recommendedMode)}>
            {t('suggestSwitchToPrefix')}{qtypes[['risk', 'sentiment', 'fundamentals', 'news', 'catalyst'].indexOf(recommendedMode)]}{t('suggestSwitchToSuffix')}
          </button>
        )}

        {beginnerMode && <div className="hermes-analysis-expectation">{t('analysisExpectationPrefix')}{qtype}{t('analysisExpectationSuffix')}</div>}

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
