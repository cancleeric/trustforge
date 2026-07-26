import { useState } from 'react'
import { BEGINNER_INTENTS, type AnalysisModeId } from '../lib/beginnerExperience'
import { useHermesI18n } from './hermesI18n'

const COINS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP']

export default function HermesFirstRun({ onStart, onSkip }: {
  onStart: (coin: string, mode: AnalysisModeId, question: string) => void
  onSkip: () => void
}) {
  const { t } = useHermesI18n()
  const [coin, setCoin] = useState('BTC')
  const [intentId, setIntentId] = useState(BEGINNER_INTENTS[0].id)
  const intent = BEGINNER_INTENTS.find((item) => item.id === intentId) ?? BEGINNER_INTENTS[0]

  return (
    <main className="hermes-first-run" aria-labelledby="first-run-title">
      <section>
        <header>
          <span>TRUSTFORGE</span>
          <button type="button" onClick={onSkip}>{t('firstRunSkip')}</button>
        </header>
        <div className="first-run-intro">
          <p>{t('firstRunEyebrowShort')}</p>
          <h1 id="first-run-title">{t('firstRunHeading')}</h1>
          <span>{t('firstRunLede')}</span>
        </div>

        <fieldset className="first-run-coins">
          <legend>{t('firstRunChooseCoin')}</legend>
          <div>{COINS.map((item) => <button key={item} type="button" aria-pressed={coin === item} onClick={() => setCoin(item)}>{item}</button>)}</div>
        </fieldset>

        <fieldset className="first-run-intents">
          <legend>{t('firstRunChooseIntent')}</legend>
          <div>{BEGINNER_INTENTS.slice(0, 4).map((item) => (
            <button key={item.id} type="button" aria-pressed={intentId === item.id} onClick={() => setIntentId(item.id)}>
              <b>{t(item.labelKey)}</b><span>{t(item.descriptionKey)}</span>
            </button>
          ))}</div>
        </fieldset>

        <div className="first-run-confirmation">
          <div><p>{t('firstRunReady')}</p><strong>{t('firstRunSummaryPrefix')}{coin}{t('firstRunSummarySeparator')}{t(intent.labelKey)}</strong><span>{t('firstRunSummaryHint')}</span></div>
          <button type="button" onClick={() => onStart(coin, intent.mode, intent.question)}>{t('firstRunCta')}</button>
        </div>
        <footer>{t('firstRunFooter')}</footer>
      </section>
    </main>
  )
}
