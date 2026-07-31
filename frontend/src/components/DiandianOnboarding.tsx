import { useState } from 'react'

const STEPS = [
  {
    text: '我是 TrustForge 的 AI 分析助手——點點！\n我的工作是幫你從各種來源中篩選出值得信任的資訊。\n接下來我會帶你了解怎麼使用這個系統，很簡單的！',
    highlight: null,
  },
  {
    text: '先選一個你想看的幣種，點它就好！',
    highlight: '.hermes-core-star, .hermes-planet',
  },
  {
    text: '按下分析，我就會去幫你蒐集多個來源的消息並打信任分數',
    highlight: '.hermes-analyze-btn, .hermes-left-rail button',
  },
  {
    text: '這裡的數字是信任分——0.8 以上很可信、0.3 以下要小心',
    highlight: '.trust-score, .confidence-gauge',
  },
  {
    text: '每個結論都可以點開看原始出處，不信的話自己驗證！',
    highlight: '.evidence-table, .evidence-trail',
  },
  {
    text: '好了！有問題隨時點右下角找我。祝分析愉快！',
    highlight: null,
  },
]

interface Props {
  onClose: () => void
}

export default function DiandianOnboarding({ onClose }: Props) {
  const [step, setStep] = useState(0)
  const [diandianState, setDiandianState] = useState<'active' | 'thinking'>('active')

  const currentStep = STEPS[step]
  const isLast = step === STEPS.length - 1

  const handleNext = () => {
    if (isLast) {
      // Mark as done
      try { localStorage.setItem('diandian_onboarding_done', '1') } catch {}
      onClose()
      return
    }
    // Brief thinking animation between steps
    setDiandianState('thinking')
    setTimeout(() => {
      setDiandianState('active')
      setStep(step + 1)
    }, 500)
  }

  const handleSkip = () => {
    try { localStorage.setItem('diandian_onboarding_done', '1') } catch {}
    onClose()
  }

  return (
    <div className="diandian-onboarding-overlay" onClick={handleSkip}>
      <div className="diandian-onboarding-card" onClick={(e) => e.stopPropagation()}>
        {/* 點點頭像 */}
        <div className="diandian-onboarding-avatar">
          <img
            src={`/diandian/${diandianState}.png`}
            alt="點點"
            className={`diandian-avatar diandian-${diandianState}`}
          />
        </div>

        {/* 對話泡泡 */}
        <div className="diandian-onboarding-bubble">
          {currentStep.text.split('\n').map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>

        {/* 按鈕 */}
        <div className="diandian-onboarding-actions">
          {step === 0 ? (
            <>
              <button className="diandian-btn-primary" onClick={handleNext}>
                好的，帶我看看
              </button>
              <button className="diandian-btn-secondary" onClick={handleSkip}>
                我自己逛就好
              </button>
            </>
          ) : (
            <>
              <button className="diandian-btn-primary" onClick={handleNext}>
                {isLast ? '開始使用！' : '下一步 →'}
              </button>
              <button className="diandian-btn-secondary" onClick={handleSkip}>
                跳過
              </button>
            </>
          )}
        </div>

        {/* 步驟指示 */}
        {step > 0 && (
          <div className="diandian-onboarding-dots">
            {STEPS.map((_, i) => (
              <span key={i} className={`diandian-dot ${i === step ? 'active' : ''}`} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
