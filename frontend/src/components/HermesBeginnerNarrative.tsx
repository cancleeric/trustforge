import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * 新手脈絡 3 步敘事入口（#demo-narrative）。
 *
 * 三大新手模組（賽道/層級卡、名詞解釋、同層/生態比較）原本只掛在 Header 右側
 * 小連結，評審 30 秒動線容易漏看。此浮層卡把它們串成「查代幣定位 → 名詞解釋 →
 * 同層/生態」一條動線，只在 beginnerMode 顯示、可關閉，不改動星系視覺化版面。
 *
 * CTA 落點刻意指向各模組「實際 live」的頁：名詞解釋（模組②）走 /asset-context
 * （glossary 標註在該頁卡片內 live），不走 /help（那只是 onboarding）。
 */

interface Step {
  no: string
  title: string
  desc: string
  ctas: { label: string; to: string }[]
}

const STEPS: Step[] = [
  {
    no: '01',
    title: '查代幣定位',
    desc: '輸入代幣（例：$ARB）→ 自動輸出 [Layer 2] 層級、結算鏈、Gas 代幣與上下游依賴。',
    ctas: [{ label: '查資產脈絡 →', to: '/asset-context' }],
  },
  {
    no: '02',
    title: '名詞解釋 + 風險提示',
    desc: 'FDV / TVL / Gas Fee / 解鎖賣壓等專有名詞自動標註，點擊看白話定義與 ⚠️ 風險提示。',
    ctas: [{ label: '看名詞標註 →', to: '/asset-context' }],
  },
  {
    no: '03',
    title: '同層比較 + 生態聯動',
    desc: '同層資產 TPS/TVL/Gas 橫向比較；官方升級事件對依賴資產的可能影響路徑。',
    ctas: [
      { label: '同層比較 →', to: '/peer-metrics' },
      { label: '生態聯動 →', to: '/eco-link' },
    ],
  },
]

export default function HermesBeginnerNarrative() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(true)
  if (!open) return null

  return (
    <div
      role="region"
      aria-label="新手脈絡 3 步"
      className="hermes-beginner-narrative"
      style={{
        position: 'absolute',
        left: '50%',
        bottom: 18,
        transform: 'translateX(-50%)',
        zIndex: 12,
        width: 'min(960px, calc(100vw - 96px))',
        background: 'rgba(6,12,22,0.92)',
        border: '1px solid rgba(77,216,224,.28)',
        borderRadius: 12,
        boxShadow: '0 20px 60px rgba(0,0,0,.6)',
        backdropFilter: 'blur(6px)',
        padding: '14px 16px 16px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span style={{ fontSize: 12, letterSpacing: '.14em', color: 'var(--color-hermes-cy,#4dd8e0)', textTransform: 'uppercase' }}>
          新手脈絡 · 3 步看懂一個代幣
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="關閉新手脈絡引導"
          style={{ background: 'transparent', border: 'none', color: 'rgba(200,220,235,.6)', cursor: 'pointer', fontSize: 13 }}
        >
          關閉 ✕
        </button>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 12,
        }}
      >
        {STEPS.map((s) => (
          <div
            key={s.no}
            style={{
              background: 'rgba(10,18,28,.85)',
              border: '1px solid rgba(140,190,210,.14)',
              borderRadius: 10,
              padding: '12px 13px',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--color-hermes-cy,#4dd8e0)', letterSpacing: '.1em' }}>{s.no}</span>
              <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--color-hermes-tx,#dce9f2)' }}>{s.title}</span>
            </div>
            <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: 'rgba(200,220,235,.72)' }}>{s.desc}</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 'auto' }}>
              {s.ctas.map((c) => (
                <button
                  key={c.to + c.label}
                  type="button"
                  onClick={() => navigate(c.to)}
                  style={{
                    background: 'rgba(77,216,224,.12)',
                    border: '1px solid rgba(77,216,224,.4)',
                    borderRadius: 6,
                    color: 'var(--color-hermes-cy,#4dd8e0)',
                    fontSize: 12,
                    padding: '5px 10px',
                    cursor: 'pointer',
                  }}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
