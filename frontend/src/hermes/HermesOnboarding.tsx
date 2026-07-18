import { useEffect, useState } from 'react'
import { rememberHermesOnboarding } from '../lib/beginnerExperience'

const steps = [
  {
    eyebrow: '第一次使用 · 1 / 3',
    title: '先選一個幣，再問一個問題',
    body: '點中央的幣種，左側選擇分析方式並輸入問題，按「交付 Hermes 執行」即可。第一次建議直接使用預設問題。',
    hint: '你不需要先理解所有分數或功能。',
  },
  {
    eyebrow: '看懂結果 · 2 / 3',
    title: '先看信任分數，再看原因',
    body: '右側的信任分數是總結：75 以上較可信、50–74 需要留意、49 以下風險較高。想知道原因，再打開「分析」查看證據與推理。',
    hint: '信任分數代表資料可信程度，不是漲跌預測或投資建議。',
  },
  {
    eyebrow: '其他工具 · 3 / 3',
    title: '需要時，再使用其他功能',
    body: '比較＝兩個幣並排看；歷史趨勢＝看信任變化；來源狀態＝檢查資料是否正常；成本＝查看系統用量。這些都不是完成第一次分析的必要步驟。',
    hint: '之後可隨時按右上角「？說明」重看。',
  },
]

export default function HermesOnboarding({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (open) setStep(0)
  }, [open])

  if (!open) return null
  const item = steps[step]
  const finish = () => {
    rememberHermesOnboarding()
    onClose()
  }

  return (
    <div className="hermes-onboarding" role="dialog" aria-modal="true" aria-labelledby="hermes-onboarding-title">
      <button className="hermes-onboarding-scrim" type="button" aria-label="關閉新手說明" onClick={finish} />
      <section className="hermes-onboarding-card">
        <div className="hermes-onboarding-eyebrow">{item.eyebrow}</div>
        <h2 id="hermes-onboarding-title">{item.title}</h2>
        <p>{item.body}</p>
        <div className="hermes-onboarding-hint">💡 {item.hint}</div>
        <div className="hermes-onboarding-progress" aria-label={`第 ${step + 1} 步，共 ${steps.length} 步`}>
          {steps.map((_, index) => <i key={index} className={index === step ? 'is-active' : ''} />)}
        </div>
        <footer>
          <button type="button" className="is-quiet" onClick={finish}>略過教學</button>
          <div>
            {step > 0 && <button type="button" className="is-secondary" onClick={() => setStep((value) => value - 1)}>上一步</button>}
            <button type="button" className="is-primary" onClick={() => step === steps.length - 1 ? finish() : setStep((value) => value + 1)}>
              {step === steps.length - 1 ? '開始使用' : '下一步'}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
