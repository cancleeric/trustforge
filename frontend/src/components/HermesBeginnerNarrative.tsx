import { useLayoutEffect, useMemo, useRef, useState } from 'react'
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
  // N33 (CEO real-browser geometry audit): at <=900px this panel used to be
  // pinned open at full content height via an empirically-measured `top:
  // 55.2vh` guess against the galaxy's orbiting chips — it stopped the panel
  // from overlapping its neighbours, but on short viewports (e.g. 680x500)
  // it also crushed the panel itself down to ~32px, i.e. unreadable. Below
  // 900px the panel now defaults to a collapsed one-line summary (intrinsic
  // height, always small, so it can never collide with the dock or galaxy
  // regardless of viewport shape — no vh guess needed) and only expands to
  // full content on demand, at which point hermes.css pins `top` to the real
  // `--hermes-top` topbar-height variable instead of a measured constant.
  // Desktop (>900px) ignores this flag entirely (see hermes.css) and always
  // renders the full content, unchanged from before.
  const [expanded, setExpanded] = useState(false)

  // N55：這塊面板貼底、高度隨內容自然長，於是是「往上長」的——視窗越窄，
  // 文字換行越多、面板越高，就越可能吃掉星系中央那顆 129x129 的 BTC 核心星鈕。
  // 在 N56 拿掉軌道環的命中攔截之後，真實滑鼠點擊判定顯示 1440x900、
  // 1280x800、1024x768（zh-TW 與 en 各一）核心星全部 BLOCKED，事件落在
  // narrative-body / narrative-header 上；1920x1080 與 <=900px 則乾淨。
  // 注意 1920x1080 乾淨而 1440x1080 中招 → 這是**寬度**驅動的碰撞，不是高度，
  // N32 當年那個 `55.2vh` 常數從一開始就抓錯變數，任何寫死比例都會在某個
  // 視窗差幾 px。所以不擬合常數，執行期實測核心星底緣當高度上限，
  // 超出內容交給既有的 overflowY:'auto' 捲動；核心星不存在時退回原 CSS 上限。
  const panelRef = useRef<HTMLDivElement | null>(null)
  const [maxHeight, setMaxHeight] = useState<string | number>('calc(100% - var(--hermes-top, 44px) - 96px)')
  useLayoutEffect(() => {
    const measure = () => {
      const panel = panelRef.current
      const core = document.querySelector('.hermes-core-star')
      if (!panel || !core) return
      const avail = Math.round(panel.getBoundingClientRect().bottom - core.getBoundingClientRect().bottom - 12)
      // 空間本來就不夠時不硬壓成不可讀的一條線，維持既有 CSS 上限。
      setMaxHeight(avail >= 96 ? avail : 'calc(100% - var(--hermes-top, 44px) - 96px)')
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [expanded])

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
      ref={panelRef}
      role="region"
      aria-label={t('beginnerNarrativeTitle')}
      className={`hermes-beginner-narrative${expanded ? ' is-expanded' : ''}`}
      style={{
        // N14：原本 left:50% + translateX(-50%) + width:min(960px,100vw-96px)，
        // 在窄視窗（例 809×650）會橫向蓋住左欄的送出按鈕，使用者點不到（hit-test
        // 落在浮層上）。改成夾在左右欄之間的「中央走道」，永遠不與左欄重疊；
        // 高度也上限化避免往上吃掉整個中央區。
        position: 'absolute',
        left: 'calc(var(--hermes-rail, 0px) + 12px)',
        right: 'calc(var(--hermes-right-rail, var(--hermes-rail, 0px)) + 12px)',
        // N53：N14 當時只把左右夾在兩條軌之間，垂直方向仍是死值 18——
        // 底部管線甲板 `.hermes-energy-deck` 高 `--hermes-bottom`
        // (clamp(94px,13.4vh,120px)，1024x768 實測 103px)，所以這塊
        // z-index:12 的面板永遠坐在 z-index:8 的甲板上面。實測 1024x768
        // 五顆階段鈕「01 來源掃描」～「05 綜合信任分數」真實點擊全部
        // BLOCKED，事件落在 narrative-body 上。改成從甲板頂緣起算，
        // 跟同檔 :534 既有寫法一致，不是為某個視窗硬調的數字。
        bottom: 'calc(var(--hermes-bottom, 0px) + 18px)',
        zIndex: 12,
        maxWidth: 960,
        marginInline: 'auto',
        maxHeight, // N55：執行期實測（見上方 useLayoutEffect）
        overflowY: 'auto',
        background: 'rgba(6,12,22,0.92)',
        border: '1px solid rgba(77,216,224,.28)',
        borderRadius: 12,
        boxShadow: '0 20px 60px rgba(0,0,0,.6)',
        backdropFilter: 'blur(6px)',
        padding: '14px 16px 16px',
      }}
    >
      <div className="hermes-beginner-narrative-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 8 }}>
        <span
          className="hermes-beginner-narrative-title"
          style={{ fontSize: 12, letterSpacing: '.14em', color: 'var(--color-hermes-cy,#4dd8e0)', textTransform: 'uppercase', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {t('beginnerNarrativeTitle')}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          {/* N33: mobile-only collapse/expand toggle (hermes.css hides this
              button entirely above 900px, where the panel always shows its
              full content as before — this control only matters on the
              short viewports that used to be crushed unreadable). Same
              24x24 minimum target as every other control on this panel. */}
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            aria-label={expanded ? t('beginnerNarrativeCollapse') : t('beginnerNarrativeExpand')}
            className="hermes-beginner-narrative-toggle"
            style={{ background: 'transparent', border: 'none', color: 'var(--color-hermes-cy,#4dd8e0)', cursor: 'pointer', fontSize: 11, minWidth: 24, minHeight: 24, display: 'none', alignItems: 'center', justifyContent: 'center', padding: '4px 6px', whiteSpace: 'nowrap' }}
          >
            {expanded ? t('beginnerNarrativeCollapse') : t('beginnerNarrativeExpand')}
          </button>
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
      </div>
      <div
        className="hermes-beginner-narrative-body"
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
