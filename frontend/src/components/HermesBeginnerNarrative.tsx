import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useHermesI18n } from '../hermes/hermesI18n'

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

export default function HermesBeginnerNarrative() {
  const navigate = useNavigate()
  const { t } = useHermesI18n()
  const [open, setOpen] = useState(true)
  const STEPS: Step[] = useMemo(() => [
    {
      no: '01',
      title: t('beginnerStep1Title'),
      desc: t('beginnerStep1Desc'),
      ctas: [{ label: t('beginnerStep1Cta'), to: '/asset-context' }],
    },
    {
      no: '02',
      title: t('beginnerStep2Title'),
      desc: t('beginnerStep2Desc'),
      ctas: [{ label: t('beginnerStep2Cta'), to: '/asset-context' }],
    },
    {
      no: '03',
      title: t('beginnerStep3Title'),
      desc: t('beginnerStep3Desc'),
      ctas: [
        { label: t('beginnerStep3CtaPeer'), to: '/peer-metrics' },
        { label: t('beginnerStep3CtaEco'), to: '/eco-link' },
      ],
    },
  ], [t])
  if (!open) return null

  return (
    <div
      role="region"
      aria-label={t('beginnerNarrativeTitle')}
      className="hermes-beginner-narrative"
      style={{
        // N14：原本 left:50% + translateX(-50%) + width:min(960px,100vw-96px)，
        // 在窄視窗（例 809×650）會橫向蓋住左欄的送出按鈕，使用者點不到（hit-test
        // 落在浮層上）。改成夾在左右欄之間的「中央走道」，永遠不與左欄重疊；
        // 高度也上限化避免往上吃掉整個中央區。
        position: 'absolute',
        left: 'calc(var(--hermes-rail, 0px) + 12px)',
        right: 'calc(var(--hermes-right-rail, var(--hermes-rail, 0px)) + 12px)',
        bottom: 18,
        zIndex: 12,
        maxWidth: 960,
        marginInline: 'auto',
        maxHeight: 'calc(100% - var(--hermes-top, 44px) - 96px)',
        overflowY: 'auto',
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
          {t('beginnerNarrativeTitle')}
        </span>
        {/* min click target ≥24x24 (was 41.6x19.5 — under the 24px min
            height on every viewport tested; this is *the* dismiss control
            for the whole overlay, so an unreachable-height target is
            especially bad). */}
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t('beginnerNarrativeCloseLabel')}
          style={{ background: 'transparent', border: 'none', color: 'rgba(200,220,235,.6)', cursor: 'pointer', fontSize: 13, minWidth: 24, minHeight: 24, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '4px 6px' }}
        >
          {t('beginnerNarrativeClose')}
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
