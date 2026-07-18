import { useState } from 'react'
import { BEGINNER_INTENTS, type AnalysisModeId } from '../lib/beginnerExperience'

const COINS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP']

export default function HermesFirstRun({ onStart, onSkip }: {
  onStart: (coin: string, mode: AnalysisModeId, question: string) => void
  onSkip: () => void
}) {
  const [coin, setCoin] = useState('BTC')
  const [intentId, setIntentId] = useState(BEGINNER_INTENTS[0].id)
  const intent = BEGINNER_INTENTS.find((item) => item.id === intentId) ?? BEGINNER_INTENTS[0]

  return (
    <main className="hermes-first-run" aria-labelledby="first-run-title">
      <section>
        <header>
          <span>TRUSTFORGE</span>
          <button type="button" onClick={onSkip}>我熟悉產品，進入完整模式</button>
        </header>
        <div className="first-run-intro">
          <p>第一次使用</p>
          <h1 id="first-run-title">你想了解哪個資產？</h1>
          <span>不用先理解分析模式或信任模型，只要選擇目的即可。</span>
        </div>

        <fieldset className="first-run-coins">
          <legend>1. 選擇資產</legend>
          <div>{COINS.map((item) => <button key={item} type="button" aria-pressed={coin === item} onClick={() => setCoin(item)}>{item}</button>)}</div>
        </fieldset>

        <fieldset className="first-run-intents">
          <legend>2. 你最想知道什麼？</legend>
          <div>{BEGINNER_INTENTS.slice(0, 4).map((item) => (
            <button key={item.id} type="button" aria-pressed={intentId === item.id} onClick={() => setIntentId(item.id)}>
              <b>{item.label}</b><span>{item.description}</span>
            </button>
          ))}</div>
        </fieldset>

        <div className="first-run-confirmation">
          <div><p>3. 準備開始</p><strong>分析 {coin}：{intent.label}</strong><span>系統會比對多個來源，整理一句結論、三個主要原因與資料限制。</span></div>
          <button type="button" onClick={() => onStart(coin, intent.mode, intent.question)}>開始第一次分析 →</button>
        </div>
        <footer>信任分析衡量資料與證據的可信程度，不預測價格，也不是投資建議。</footer>
      </section>
    </main>
  )
}
