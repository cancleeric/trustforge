import { useState } from 'react'

const STEPS = [
  {
    text: '我是 TrustForge 的 AI 分析助手——點點！\n我的工作是幫你從各種來源中篩選出值得信任的資訊。\n接下來我會帶你了解怎麼使用這個系統，很簡單的！',
    highlight: null,
    position: '',
  },
  {
    text: '畫面中央的圓球就是幣種，點它就能選擇你想分析的幣！\n（目前支援 BTC、ETH、SOL、BNB、XRP）',
    highlight: '.hermes-core-star, .hermes-planet',
    position: '👆 畫面正中央的星球',
  },
  {
    text: '選好幣之後，看左邊的輸入框——輸入你的問題，按下「立即分析」按鈕\n我就會去幫你蒐集多個來源的消息並計算信任分數',
    highlight: '.hermes-analyze-btn, .hermes-left-rail button',
    position: '👈 畫面左側輸入區',
  },
  {
    text: '右邊面板會顯示信任分數圓環——0.8 以上很可信、0.3 以下要小心\n下方還有四維信任拆解：信譽、佐證、即時性、抗操縱',
    highlight: '.trust-score, .confidence-gauge',
    position: '👉 畫面右側數字面板',
  },
  {
    text: '底部那一排是執行管線（來源掃描 → 信任過濾 → 交叉驗證 → 操縱偵測 → 綜合評分）\n點任何一個節點都能展開看詳細內容！',
    highlight: '.hermes-energy-station',
    position: '👇 畫面最下方那一排',
  },
  {
    text: '每個結論都可以點開看原始出處，不信的話自己驗證！\n在分析報告的 Evidence 區塊，每筆證據都帶有來源連結和取得時間',
    highlight: '.evidence-table, .evidence-trail',
    position: '📋 分析完成後的報告區',
  },
  {
    text: '好了！有問題隨時點 Hermes 欄位右上角找我。祝分析愉快！',
    highlight: null,
    position: '👇 右下角就是我啦',
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
      try { document.cookie = 'diandian_onboarding_done=1; path=/; max-age=31536000; SameSite=Lax' } catch {}
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
    try { document.cookie = 'diandian_onboarding_done=1; path=/; max-age=31536000; SameSite=Lax' } catch {}
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
          {currentStep.position && (
            <p style={{ marginTop: '8px', fontSize: '13px', color: '#4dd8e0', fontWeight: 600 }}>
              {currentStep.position}
            </p>
          )}
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
