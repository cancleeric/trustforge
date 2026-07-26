import { useEffect, useRef } from 'react'
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
  // N42: 訊息串永遠停在 scrollTop 0，最新一則被切在容器下緣——實測 14 組
  // （2 locale × 7 視窗）全部 `scrollTop: 0`，而 scrollHeight 最高 1403、
  // clientHeight 只有 140。也就是說使用者從來看不到 HERMES 剛剛回了什麼，
  // 得自己往下捲。沒有任何 agent 介面是這樣的，這正是「隨便一個 ai agent
  // 都比這個強」的核心。新訊息進來就釘到底，跟一般聊天介面一致。
  const transcriptRef = useRef<HTMLDivElement>(null)
  const transcriptDeps = `${questionContext?.conversation?.length ?? 0}|${hermesMessage}`
  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [transcriptDeps])
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

        {/* N40: 這塊原本不是「對話」，是三種東西疊在一起——一張 amber 的
            agentOutput 卡（最新回覆）、一張標題寫「對話記憶」而內容是
            `HERMES › …` 前綴純文字的 10.5px 小卡（只留最後 3 則），再加一段
            RAG 相似問題清單。三者用同一個捲動容器，所以歷史訊息、最新回覆、
            系統建議在視覺上分不出誰是誰，也看不出誰先誰後。老闆的原話是
            「隨便一個 ai agent 都比這個強」，這是對的：沒有任何對話介面長這樣。

            改成一般 agent 介面的三段式：
              transcript  單一時間序訊息串，靠左右對齊分角色（使用者靠右、
                          HERMES 靠左），不再用 `HERMES ›` 前綴。字級
                          10.5→12.5，並且不再 `.slice(-3)`——訊息串本來就會捲，
                          砍掉歷史沒有理由。
              suggestions RAG 相似問題移出訊息串，收進預設闔起的 <details>。
                          那是系統建議、不是對話內容，混在一起就是雜訊。
              composer    模式選單＋輸入框＋送出鍵包成一塊，flexShrink:0
                          釘在底部（見下方 N40 composer）。
            角色標籤只在 HERMES 一側顯示品牌字 `HERMES`；使用者一側靠右對齊
            即可辨識，不加字就不必新增 i18n key（少一處可能漏翻的地方）。 */}
        <div
          ref={transcriptRef}
          aria-label={t('agentOutput')}
          style={{ flex: 1, minHeight: 140, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 10, paddingRight: 2 }}
        >
          {(() => {
            const history = (questionContext?.conversation ?? [])
              .filter((msg, i, arr) => i === 0 || msg.role !== arr[i - 1].role || msg.content !== arr[i - 1].content)
            const bubbles = history.map((msg) => ({ key: msg.message_id, role: msg.role, text: msg.content, latest: false }))
            // 最新回覆有可能已經是 conversation 的最後一則（後端回寫），重複
            // 顯示同一句話會讓人以為 HERMES 說了兩次，所以只在不同時才補上。
            const lastHermes = [...history].reverse().find((m) => m.role === 'hermes')
            if (hermesMessage && lastHermes?.content !== hermesMessage) {
              bubbles.push({ key: 'latest', role: 'hermes', text: hermesMessage, latest: true })
            } else if (bubbles.length) {
              bubbles[bubbles.length - 1].latest = bubbles[bubbles.length - 1].role === 'hermes'
            }
            // 還沒有任何往返時，把待送出的指令當成使用者的第一顆泡泡，讓畫面
            // 一開始就是對話的樣子，而不是一行 `> risk: …` 的 log。
            if (!bubbles.length && hasOrder) bubbles.push({ key: 'pending', role: 'user', text: query || qtype, latest: false })
            return bubbles.map((b) => {
              const mine = b.role !== 'hermes'
              return (
                <div key={b.key} style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: '92%', flexShrink: 0 }}>
                  {!mine && <div style={{ fontSize: 9, letterSpacing: 1, color: b.latest ? 'var(--color-hermes-amber)' : 'var(--color-hermes-tx3)', marginBottom: 3 }}>HERMES</div>}
                  <div
                    style={{
                      background: mine ? 'rgba(40,64,90,.35)' : 'var(--color-hermes-inset)',
                      border: `1px solid ${b.latest ? 'var(--color-hermes-amber)' : mine ? 'var(--color-hermes-bd2)' : 'var(--color-hermes-bd)'}`,
                      borderRadius: mine ? '8px 8px 2px 8px' : '2px 8px 8px 8px',
                      padding: '9px 11px',
                      fontSize: 12.5,
                      lineHeight: 1.6,
                      color: mine ? 'var(--color-hermes-tx2)' : 'var(--color-hermes-tx)',
                      overflowWrap: 'anywhere',
                    }}
                  >
                    {b.text}
                  </div>
                </div>
              )
            })
          })()}
        </div>

        {!!questionContext && (
          <details style={{ flexShrink: 0, marginBottom: 10, borderTop: '1px solid var(--color-hermes-bd)', paddingTop: 7 }}>
            <summary style={{ fontSize: 9, letterSpacing: 1, color: 'var(--color-hermes-cyan)', cursor: 'pointer', minHeight: 24, display: 'flex', alignItems: 'center' }}>
              <GlossaryTerm term="rag" label={t('similarQuestions')} compact />
            </summary>
            <div role="note" style={{ fontSize: 9.5, lineHeight: 1.35, color: 'var(--color-hermes-amber)', margin: '5px 0' }}>
              {t('historyDisclaimer')}
            </div>
            {questionContext.matches.length ? questionContext.matches.slice(0, 3).map((match) => (
              <button key={match.question_id} type="button" onClick={() => onRecallQuestion?.(match.question)} title={match.answer ?? '尚無完成快照'}
                style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 0, borderBottom: '1px solid var(--color-hermes-bd)', color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 11, lineHeight: 1.4, padding: '7px 2px', minHeight: 24, cursor: 'pointer' }}>
                <b style={{ color: 'var(--color-hermes-amber)' }}>{Math.round(match.similarity * 100)}%</b> · {match.coin}/{modeLabel(match.mode, t)} · {match.question}
              </button>
            )) : (
              <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--color-hermes-tx3)', padding: '5px 2px' }}>{t('noSimilarQuestions')}</div>
            )}
          </details>
        )}

        <label style={{ display: 'block', fontSize: 10, color: 'var(--color-hermes-tx2)', marginBottom: 5 }}>{t('analysisMode')}</label>
        <div style={{ position: 'relative', marginBottom: 10, flexShrink: 0 }}>
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
        {/* N40 composer：輸入區整塊 flexShrink:0，訊息串（flex:1）吃剩下的高度，
            這是一般 agent 介面的配置——內容多的時候捲訊息，不是壓輸入框。 */}
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
          style={{ width: '100%', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: 'var(--color-hermes-amber)', border: 'none', borderRadius: 5, color: '#1a1206', fontWeight: 700, fontSize: 12, padding: 9, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .55 : 1, transition: 'filter .15s, transform .08s' }}
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
