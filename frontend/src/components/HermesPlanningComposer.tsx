import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useHermesI18n, type HermesLocale } from '../hermes/hermesI18n'
import { secureRandomUuid } from '../lib/uuid'
import {
  previewAnalysisPlan,
  type AnalysisPlan,
  type AnalysisPlanErrorCode,
} from '../lib/analysisPlan'

type Phase =
  | { kind: 'idle' }
  | { kind: 'loading'; requestId: number }
  | { kind: 'ready'; plan: AnalysisPlan; question: string; hints: string[]; locale: HermesLocale }
  | { kind: 'error'; code: string; retryable: boolean }

const copy = {
  'zh-TW': {
    eyebrow: 'HERMES · 規劃預覽',
    title: '先看分析會怎麼進行',
    intro: '用自然語言描述需求，Hermes 只會產生可檢查的分析計畫；此步驟不會建立或執行正式工作。',
    question: '你想分析什麼？',
    placeholder: '例如：比較 BTC 與 ETH 近期機構資金流，並檢查監管新聞是否改變風險。',
    examples: '官方題型範例（只填入，不會送出）',
    hints: '資產提示（選填）',
    hintsHelp: '以逗號分隔，例如 BTC, ETH；最多 8 個。',
    preview: '預覽分析計畫',
    loading: 'Hermes 正在整理分析路徑…',
    cancel: '取消',
    cancelled: '已取消規劃預覽。',
    invalidQuestion: '請輸入要分析的問題。',
    invalidHints: '資產提示須為大寫代號，以逗號分隔（最多 8 個且不可重複）。',
    stale: '你已修改問題或資產提示；以下仍是上一版預覽。',
    ready: '計畫已就緒',
    clarify: '需要先釐清',
    assets: '偵測到的資產',
    intents: '分析意圖',
    sources: '預計使用的來源類型',
    strategy: '分析策略',
    questions: '需要你補充',
    warnings: '注意事項',
    confidence: '規劃確定度',
    confidenceDisclaimer: '規劃確定度只表示問題是否足以形成分析路徑，不是信任分數，也不是校準後資訊完整度。',
    provenance: '規劃來源',
    retry: '再試一次',
    edit: '返回編輯',
    noAssets: '尚未確認',
    noItems: '尚未指定',
    previewOnly: '僅供預覽 · 不會建立正式分析工作',
  },
  en: {
    eyebrow: 'HERMES · PLAN PREVIEW',
    title: 'Preview how the analysis will run',
    intro: 'Describe your goal in plain language. Hermes returns an inspectable plan only; this step never creates or executes a formal job.',
    question: 'What do you want to analyze?',
    placeholder: 'For example: compare recent institutional flows for BTC and ETH and check whether regulatory news changes the risk.',
    examples: 'Official examples (fill only; never submitted automatically)',
    hints: 'Asset hints (optional)',
    hintsHelp: 'Comma-separated, for example BTC, ETH; up to 8.',
    preview: 'Preview analysis plan',
    loading: 'Hermes is mapping the analysis path…',
    cancel: 'Cancel',
    cancelled: 'Plan preview cancelled.',
    invalidQuestion: 'Enter a question to analyze.',
    invalidHints: 'Use comma-separated uppercase asset symbols (up to 8, without duplicates).',
    stale: 'You changed the question or asset hints. This is still the previous preview.',
    ready: 'Plan ready',
    clarify: 'Clarification needed',
    assets: 'Detected assets',
    intents: 'Analysis intents',
    sources: 'Planned source classes',
    strategy: 'Analysis strategy',
    questions: 'Questions for you',
    warnings: 'Warnings',
    confidence: 'Planning confidence',
    confidenceDisclaimer: 'Planning confidence only indicates whether the question supports a clear analysis path. It is not a trust score or calibrated information completeness.',
    provenance: 'Plan provenance',
    retry: 'Try again',
    edit: 'Back to edit',
    noAssets: 'Not confirmed',
    noItems: 'Not specified',
    previewOnly: 'Preview only · No formal analysis job is created',
  },
} as const

const officialExamples = {
  'zh-TW': [
    {
      label: '多源整合',
      question: '分析 SOL 過去兩週，整合價格、鏈上活躍、新聞與社群熱度，給出整體狀態及各類資料的一致程度。',
      hints: 'SOL',
    },
    {
      label: '假設驗證',
      question: '對「BTC 短期將盤整」蒐集正反證據，說明最終判斷與理由。',
      hints: 'BTC',
    },
    {
      label: '比較分析',
      question: '比較 BTC 與 ETH 當前市場位置及風險特徵，包括流動性、關注度與風險敞口。',
      hints: 'BTC, ETH',
    },
  ],
  en: [
    {
      label: 'Multi-source',
      question: 'Analyze SOL over the past two weeks using price, on-chain activity, news, and social attention; report the overall state and how consistently the sources agree.',
      hints: 'SOL',
    },
    {
      label: 'Test a hypothesis',
      question: 'Collect evidence for and against the hypothesis that BTC will consolidate in the short term, then explain the final judgment.',
      hints: 'BTC',
    },
    {
      label: 'Compare assets',
      question: 'Compare the current market position and risk profile of BTC versus ETH, including liquidity, attention, and risk exposure.',
      hints: 'BTC, ETH',
    },
  ],
} as const

const localizedErrors = {
  'zh-TW': {
    invalid_plan_request: '請檢查問題、語系與資產提示格式。',
    plan_rate_limited: '規劃請求過於頻繁，請稍後再試。',
    plan_temporarily_unavailable: 'Hermes 規劃暫時不可用，請稍後再試。',
    plan_timeout: 'Hermes 規劃逾時，請再試一次。',
    timeout: 'Hermes 規劃請求逾時。',
    network_error: '目前無法連線至 Hermes 規劃服務。',
    parse_error: 'Hermes 規劃服務回應格式不符預期。',
    cancelled: '規劃預覽已取消。',
  },
  en: {
    invalid_plan_request: 'Check the question, locale, and asset hint format.',
    plan_rate_limited: 'Too many planning requests. Try again shortly.',
    plan_temporarily_unavailable: 'Hermes planning is temporarily unavailable. Try again shortly.',
    plan_timeout: 'Hermes planning timed out. Try again.',
    timeout: 'The Hermes planning request timed out.',
    network_error: 'Hermes planning cannot be reached right now.',
    parse_error: 'The Hermes planning response did not match the expected format.',
    cancelled: 'Plan preview cancelled.',
  },
} as const

const ASSET_HINT = /^[A-Z0-9][A-Z0-9._:-]{0,15}$/

function parseHints(value: string): string[] | null {
  const hints = value.split(',').map((hint) => hint.trim()).filter(Boolean)
  if (hints.length > 8 || new Set(hints).size !== hints.length || hints.some((hint) => !ASSET_HINT.test(hint))) {
    return null
  }
  return hints
}

function errorIsRetryable(code: string): boolean {
  return (['plan_rate_limited', 'plan_temporarily_unavailable', 'plan_timeout', 'timeout', 'network_error'] as string[]).includes(code)
}

export default function HermesPlanningComposer() {
  const { locale } = useHermesI18n()
  const text = copy[locale]
  const [question, setQuestion] = useState('')
  const [hintText, setHintText] = useState('')
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [notice, setNotice] = useState('')
  const requestSequence = useRef(0)
  const inFlightRequest = useRef<number | null>(null)
  const activeController = useRef<AbortController | null>(null)
  const currentLocale = useRef(locale)
  currentLocale.current = locale
  const questionRef = useRef<HTMLTextAreaElement | null>(null)
  const resultHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const errorRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => () => activeController.current?.abort(), [])
  useEffect(() => {
    if (phase.kind === 'ready') resultHeadingRef.current?.focus()
    if (phase.kind === 'error') errorRef.current?.focus()
  }, [phase.kind])

  const parsedHints = useMemo(() => parseHints(hintText), [hintText])
  const isStale = phase.kind === 'ready' && (
    phase.question !== question.trim() ||
    parsedHints === null ||
    phase.hints.join('\n') !== parsedHints.join('\n') ||
    phase.locale !== locale
  )

  async function requestPreview() {
    if (inFlightRequest.current !== null) return
    const normalizedQuestion = question.trim()
    setNotice('')
    if (!normalizedQuestion) {
      setPhase({ kind: 'error', code: 'invalid_question', retryable: false })
      return
    }
    if (parsedHints === null) {
      setPhase({ kind: 'error', code: 'invalid_hints', retryable: false })
      return
    }

    const controller = new AbortController()
    activeController.current = controller
    const requestId = ++requestSequence.current
    inFlightRequest.current = requestId
    const requestLocale = locale
    setPhase({ kind: 'loading', requestId })
    try {
      const response = await previewAnalysisPlan({
        question: normalizedQuestion,
        locale: requestLocale,
        ...(parsedHints.length ? { asset_hints: parsedHints } : {}),
        client_request_id: secureRandomUuid(),
      }, controller.signal)
      if (controller.signal.aborted || requestSequence.current !== requestId) return
      if (currentLocale.current !== requestLocale) {
        setPhase({ kind: 'idle' })
        return
      }
      if (response.ok) {
        setPhase({ kind: 'ready', plan: response.data, question: normalizedQuestion, hints: parsedHints, locale: requestLocale })
        return
      }
      const code = response.error.code as AnalysisPlanErrorCode | string
      setPhase({
        kind: 'error',
        code,
        retryable: 'retryable' in response.error ? response.error.retryable === true : errorIsRetryable(code),
      })
    } finally {
      if (inFlightRequest.current === requestId) inFlightRequest.current = null
      if (activeController.current === controller) activeController.current = null
    }
  }

  function cancelPreview() {
    requestSequence.current += 1
    activeController.current?.abort()
    activeController.current = null
    inFlightRequest.current = null
    setPhase({ kind: 'idle' })
    setNotice(text.cancelled)
  }

  return (
    <main className="relative mx-auto min-h-[calc(100vh-57px)] max-w-6xl px-4 py-7 sm:px-6">
      <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_50%_0%,var(--color-tf-bg-hero)_0%,var(--color-tf-bg)_72%)]" />
      <header className="mb-6 max-w-3xl">
        <p className="font-mono text-xs font-semibold uppercase tracking-[1.6px] text-tf-link">{text.eyebrow}</p>
        <h1 className="mt-2 text-2xl font-bold text-tf-text sm:text-3xl">{text.title}</h1>
        <p className="mt-2 text-sm leading-6 text-tf-text2">{text.intro}</p>
        <p className="mt-3 inline-flex rounded-full border border-tf-link/40 bg-tf-accent/10 px-3 py-1 font-mono text-xs text-tf-link">
          {text.previewOnly}
        </p>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <form
          className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4 sm:p-5"
          onSubmit={(event) => { event.preventDefault(); void requestPreview() }}
        >
          <label className="block text-sm font-semibold text-tf-text" htmlFor="planning-question">{text.question}</label>
          <textarea
            id="planning-question"
            ref={questionRef}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={1000}
            rows={5}
            disabled={phase.kind === 'loading'}
            placeholder={text.placeholder}
            className="mt-2 w-full resize-y rounded-md border border-tf-border bg-tf-bg px-3 py-2 text-sm leading-6 text-tf-text placeholder:text-tf-muted focus:border-tf-link focus:outline-none"
            onKeyDown={(event) => {
              if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
              event.preventDefault()
              void requestPreview()
            }}
          />
          <fieldset className="mt-3" disabled={phase.kind === 'loading'}>
            <legend className="text-xs text-tf-muted">{text.examples}</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {officialExamples[locale].map((example) => (
                <button
                  key={example.label}
                  type="button"
                  onClick={() => {
                    setQuestion(example.question)
                    setHintText(example.hints)
                    setPhase({ kind: 'idle' })
                    setNotice('')
                    window.setTimeout(() => questionRef.current?.focus(), 0)
                  }}
                  className="min-h-9 rounded-full border border-tf-border px-3 py-1 text-xs font-semibold text-tf-text2 hover:border-tf-link hover:text-tf-link"
                >
                  {example.label}
                </button>
              ))}
            </div>
          </fieldset>
          <div className="mt-4">
            <label className="block text-sm font-semibold text-tf-text" htmlFor="planning-assets">{text.hints}</label>
            <input
              id="planning-assets"
              value={hintText}
              onChange={(event) => setHintText(event.target.value)}
              disabled={phase.kind === 'loading'}
              placeholder="BTC, ETH"
              aria-describedby="planning-assets-help"
              className="mt-2 w-full rounded-md border border-tf-border bg-tf-bg px-3 py-2 text-sm text-tf-text placeholder:text-tf-muted focus:border-tf-link focus:outline-none"
            />
            <p id="planning-assets-help" className="mt-1 text-xs text-tf-muted">{text.hintsHelp}</p>
          </div>
          <div className="mt-5 flex flex-wrap gap-3">
            {phase.kind === 'loading' ? (
              <button type="button" onClick={cancelPreview} className="min-h-11 rounded-md border border-tf-warn px-5 py-2 text-sm font-semibold text-tf-warn">
                {text.cancel}
              </button>
            ) : (
              <button type="submit" className="min-h-11 rounded-md bg-tf-accent px-5 py-2 text-sm font-bold text-tf-bg hover:opacity-90">
                {text.preview} <span aria-hidden="true">→</span>
              </button>
            )}
          </div>
        </form>

        <section className="min-h-72 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-5" aria-live="polite" aria-busy={phase.kind === 'loading'}>
          {notice && <p role="status" className="rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">{notice}</p>}
          {phase.kind === 'idle' && !notice && (
            <div className="flex min-h-64 items-center justify-center text-center text-sm text-tf-muted">{text.previewOnly}</div>
          )}
          {phase.kind === 'loading' && (
            <div role="status" className="flex min-h-64 items-center justify-center gap-3 text-sm text-tf-text2">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-tf-border border-t-tf-link" aria-hidden="true" />
              {text.loading}
            </div>
          )}
          {phase.kind === 'error' && (
            <div ref={errorRef} tabIndex={-1} role="alert" className="rounded-md border border-tf-danger/50 bg-tf-danger/10 p-4">
              <p className="font-semibold text-tf-danger">
                {phase.code === 'invalid_question'
                  ? text.invalidQuestion
                  : phase.code === 'invalid_hints'
                    ? text.invalidHints
                    : localizedErrors[locale][phase.code as keyof (typeof localizedErrors)[typeof locale]] ?? localizedErrors[locale].parse_error}
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                {phase.retryable && <button type="button" onClick={() => void requestPreview()} className="min-h-11 rounded-md border border-tf-link px-4 py-2 text-sm font-semibold text-tf-link">{text.retry}</button>}
                <button type="button" onClick={() => {
                  setPhase({ kind: 'idle' })
                  window.setTimeout(() => questionRef.current?.focus(), 0)
                }} className="min-h-11 rounded-md border border-tf-border px-4 py-2 text-sm text-tf-text2">{text.edit}</button>
              </div>
            </div>
          )}
          {phase.kind === 'ready' && <PlanResult headingRef={resultHeadingRef} plan={phase.plan} stale={isStale} text={text} />}
        </section>
      </div>
    </main>
  )
}

function PlanResult({
  plan,
  stale,
  text,
  headingRef,
}: {
  plan: AnalysisPlan
  stale: boolean
  text: (typeof copy)[keyof typeof copy]
  headingRef: RefObject<HTMLHeadingElement | null>
}) {
  return (
    <article>
      {stale && <p role="status" className="mb-4 rounded-md border border-tf-warn/60 bg-tf-warn/10 p-3 text-sm text-tf-warn">{text.stale}</p>}
      <p className="font-mono text-xs uppercase tracking-wide text-tf-link">
        {plan.outcome === 'ready' ? text.ready : text.clarify}
      </p>
      <h2 ref={headingRef} tabIndex={-1} className="mt-2 text-xl font-bold text-tf-text">{text.strategy}</h2>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tf-text2">{plan.strategy_summary}</p>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <PlanList title={text.assets} items={plan.detected_assets} empty={text.noAssets} />
        <PlanList title={text.sources} items={plan.source_classes} empty={text.noItems} />
      </div>
      <section className="mt-5">
        <h3 className="text-sm font-semibold text-tf-text">{text.intents}</h3>
        <ul className="mt-2 grid gap-2">
          {plan.intents.map((intent, index) => (
            <li key={`${intent.label}-${index}`} className="rounded-md border border-tf-border bg-tf-bg p-3">
              <p className="text-sm font-semibold text-tf-text">{intent.label}</p>
              <p className="mt-1 whitespace-pre-wrap text-sm text-tf-text2">{intent.rationale}</p>
            </li>
          ))}
        </ul>
      </section>
      {plan.clarifications.length > 0 && (
        <section className="mt-5">
          <h3 className="text-sm font-semibold text-tf-text">{text.questions}</h3>
          <ol className="mt-2 grid gap-3">
            {plan.clarifications.map((item) => (
              <li key={item.id} className="rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
                <p>{item.question}</p>
                {item.options.length > 0 && (
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {item.options.map((option) => <li key={option}>{option}</li>)}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
      {plan.warnings.length > 0 && <PlanList title={text.warnings} items={plan.warnings} empty={text.noItems} />}
      <section className="mt-5 rounded-md border border-tf-border bg-tf-bg p-3">
        <h3 className="text-sm font-semibold text-tf-text">{text.confidence}: {plan.confidence.level}</h3>
        <p className="mt-1 whitespace-pre-wrap text-sm text-tf-text2">{plan.confidence.rationale}</p>
        <p className="mt-2 text-xs text-tf-muted">{text.confidenceDisclaimer}</p>
      </section>
      <section className="mt-3 text-xs text-tf-muted">
        <h3 className="font-semibold text-tf-text2">{text.provenance}</h3>
        <p className="mt-1 font-mono">{plan.provenance.planner} · {plan.provenance.provider} · {plan.provenance.policy_version}</p>
      </section>
    </article>
  )
}

function PlanList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section className="mt-5 first:mt-0">
      <h3 className="text-sm font-semibold text-tf-text">{title}</h3>
      {items.length ? (
        <ul className="mt-2 flex flex-wrap gap-2">
          {items.map((item, index) => <li key={`${item}-${index}`} className="rounded border border-tf-border bg-tf-bg px-2 py-1 text-sm text-tf-text2">{item}</li>)}
        </ul>
      ) : <p className="mt-2 text-sm text-tf-muted">{empty}</p>}
    </section>
  )
}
