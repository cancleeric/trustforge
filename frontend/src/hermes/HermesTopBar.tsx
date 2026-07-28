import type { ReactNode } from 'react'
import { useHermesI18n } from './hermesI18n'
import type { HermesWorkspaceModule } from './HermesModuleDeck'

interface HermesTopBarProps {
  costLedger?: number | null
  version?: string
  systemId?: string
  activeModule?: HermesWorkspaceModule | null
  onModuleSelect?: (module: HermesWorkspaceModule) => void
  onHome?: () => void
  degradedMessage?: string | null
  onToggleShip?: () => void
  onHelp?: () => void
  beginnerMode?: boolean
  onBeginnerModeChange?: (enabled: boolean) => void
  reducedMotion?: boolean
  onReducedMotionToggle?: () => void
  runtimeStatus?: ReactNode
}

export default function HermesTopBar({
  costLedger = null,
  version = '… · GALAXY', // 同 HermesDashboard：預設值不要長得像真的版號
  systemId = 'SYS·HRM-01',
  activeModule = null,
  onModuleSelect,
  onHome,
  degradedMessage = null,
  onToggleShip,
  onHelp,
  beginnerMode = false,
  onBeginnerModeChange,
  reducedMotion = false,
  onReducedMotionToggle,
  runtimeStatus,
}: HermesTopBarProps) {
  const { locale, setLocale, t } = useHermesI18n()
  // 後端沒走發版流程時 /api/health 會回 version: "dev"。那不是版號，是「這台
  // 沒被建置過」的哨兵值；照原樣用一般樣式印出來，看起來就像系統版本叫 dev。
  // 標成警示色並掛說明，讓它讀起來是狀態而不是版本名。
  const isUnbuiltVersion = /^dev\b/.test(version)
  const navItems = [
    { id: 'analyze' as const, label: t('analyze'), description: locale === 'zh-TW' ? '找出風險、原因與可追溯證據' : 'Find risks, reasons, and traceable evidence' },
    { id: 'compare' as const, label: t('compare'), description: locale === 'zh-TW' ? '並排比較兩個資產的可信狀態' : 'Compare two assets side by side' },
    { id: 'history' as const, label: t('history'), description: locale === 'zh-TW' ? '查看信任與資料完整度如何變化' : 'Review trust and completeness over time' },
    { id: 'status' as const, label: t('sources'), description: locale === 'zh-TW' ? '確認資料來源是否正常更新' : 'Check whether sources are updating' },
    { id: 'costs' as const, label: t('costs'), description: locale === 'zh-TW' ? '查看分析使用量與模型費用' : 'Review analysis usage and model cost' },
  ]
  return (
    <div
      className="hermes-topbar"
      style={{
        position: 'absolute', left: 0, right: 0, top: 0, height: 'var(--hermes-top)', zIndex: 10,
        display: 'flex', alignItems: 'center', gap: 14, padding: '0 20px',
        background: 'rgba(10,16,24,.62)', backdropFilter: 'blur(10px)',
        borderBottom: '1px solid var(--color-hermes-bd)',
        boxShadow: '0 1px 12px rgba(77,216,224,.08)',
      }}
    >
      {/* N29: min click target ≥24x24 (was 184.8x19.5 — under the 24px min
          height on every viewport tested); minHeight + flex-centering keeps
          the visual logo/label unchanged while guaranteeing the tap target. */}
      <button type="button" onClick={onHome} aria-label={t('homeAria')} style={{ display: 'flex', alignItems: 'center', gap: 9, border: 0, padding: 0, background: 'transparent', fontFamily: 'inherit', cursor: 'pointer', minHeight: 24 }}>
        <div style={{ width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)', border: '1.5px solid var(--color-hermes-cyan)', borderRadius: 2 }}>
          <div style={{ position: 'absolute', inset: 3, background: 'var(--color-hermes-cyan)', opacity: 0.85 }} />
        </div>
        <span style={{ fontWeight: 700, fontSize: 13, letterSpacing: '1.6px', color: 'var(--color-hermes-tx)' }}>
          TRUSTFORGE <span style={{ color: 'var(--color-hermes-cyan)' }}>HERMES</span>
        </span>
      </button>
      {/* 版號與系統代號原本吃 --color-hermes-tx3（#526375），在頂欄那層
          rgba(10,16,24,.62) 疊 #02040a 的底上實測只有 3.2:1，而且字級只有
          9~10px——CEO 直接回報「版號在哪裡？我怎麼畫面看不到」。改吃 tx2
          （#7f97ab，6.4:1）。這兩格是識別資訊，不是裝飾文字，不該用最淡的階。 */}
      <span style={{ fontSize: 9, color: 'var(--color-hermes-tx2)', letterSpacing: 1 }}>✛ {systemId}</span>
      <span
        title={isUnbuiltVersion ? t('versionDevHint') : undefined}
        style={{ fontSize: 10, color: isUnbuiltVersion ? 'var(--color-hermes-amber)' : 'var(--color-hermes-tx2)', border: `1px solid ${isUnbuiltVersion ? 'rgba(232,179,77,.4)' : 'var(--color-hermes-bd2)'}`, borderRadius: 4, padding: '2px 7px' }}
      >{version}</span>
      <span className="hermes-uplink-status" title={degradedMessage || undefined} aria-label={degradedMessage ? t('degradedState') : t('liveUplink')} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', background: degradedMessage ? 'rgba(232,179,77,.13)' : 'rgba(77,216,224,.13)', border: `1px solid ${degradedMessage ? 'rgba(232,179,77,.4)' : 'rgba(77,216,224,.4)'}`, borderRadius: 4, padding: '2px 8px' }}>
        <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', animation: 'hermes-pulse 1.8s infinite' }} />
        {/* N28: on very narrow phones (≤430px) the topbar has no room left
            after logo + toggles for this label text without pushing trailing
            controls (e.g. the language toggle) off-screen. The parent span
            keeps its aria-label/title with the full text, so this is a
            visual-only collapse, not a loss of information. */}
        <span className="hermes-uplink-status-label" aria-hidden="true">{degradedMessage ? t('degradedState') : t('liveUplink')}</span>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--color-hermes-amber)', background: 'rgba(232,179,77,.13)', border: '1px solid rgba(232,179,77,.4)', borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, transform: 'rotate(45deg)', background: 'var(--color-hermes-amber)', animation: 'hermes-pulse 2.4s infinite' }} />{t('systemActivePrefix')} {t('active')}
      </span>
      <nav className="hermes-topbar-nav" aria-label={t('navigation')} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        {navItems.filter((item) => !beginnerMode || item.id === 'analyze').map((item) => (
          <button
            type="button"
            key={item.id}
            className="hermes-nav-item"
            onClick={() => onModuleSelect?.(item.id)}
            aria-pressed={activeModule === item.id}
            aria-description={item.description}
            data-description={item.description}
            style={{
              color: activeModule === item.id ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-tx2)',
              fontSize: 9, letterSpacing: '.7px', textDecoration: 'none', padding: '4px 6px',
              border: 0, borderBottom: activeModule === item.id ? '1px solid var(--color-hermes-cyan)' : '1px solid transparent',
              background: 'transparent', fontFamily: 'inherit', cursor: 'pointer',
              /* N24: this button's own padding/font only produced a 22.5px
                 tall hit target (under the 24x24 minimum), visibly shorter
                 than the sibling toolbar buttons in the same row (33-39px).
                 minHeight + flex-centering keeps the visual label/padding
                 unchanged (incl. the active-state border-bottom) while
                 guaranteeing the tap target. */
              minHeight: 24, display: 'inline-flex', alignItems: 'center',
            }}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      {runtimeStatus}
      <button type="button" className="hermes-mode-toggle" onClick={() => onBeginnerModeChange?.(!beginnerMode)} aria-pressed={beginnerMode}>
        {beginnerMode ? t('beginnerModeOn') : t('beginnerModeOff')}
      </button>
      {/* N62：這顆按鈕原本掛 aria-label（「啟用低動態模式」），而 aria-label 是
          「取代」不是「補充」可見文字——螢幕閱讀器唸到的名稱跟眼睛看到的
          「動態」對不上，語音操作使用者照著唸也點不到（WCAG 2.5.3 Label in
          Name）。而且兩個分支語意還互相打架：一邊是動作、一邊是狀態。改成跟
          旁邊那顆模式切換一樣，讓可見文字直接當可及名稱，狀態交給
          aria-pressed，動作提示留在 title。 */}
      <button type="button" className="hermes-mode-toggle" onClick={onReducedMotionToggle} aria-pressed={reducedMotion} title={reducedMotion ? t('reducedMotionOnTitle') : t('reducedMotionOffTitle')}>
        {reducedMotion ? t('dynamicOff') : t('dynamicOn')}
      </button>
      <button type="button" className="hermes-help-toggle" onClick={onHelp} aria-label={t('openBeginnerHelp')}>{t('helpToggle')}</button>
      {!beginnerMode && <button type="button" className="hermes-ship-toggle" onClick={onToggleShip}>{t('shipToggle')}</button>}
      {/* min click target ≥24x24 (was 26.8x21.5 — under the 24px min height
          on every viewport tested); minHeight + flex-centering keeps the
          visual label/padding unchanged while guaranteeing the tap target. */}
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} style={{ background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 4, color: 'var(--color-hermes-tx2)', fontFamily: 'inherit', fontSize: 9, padding: '3px 7px', minWidth: 24, minHeight: 24, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      {!beginnerMode && <span style={{ fontSize: 10, color: 'var(--color-hermes-tx2)' }}>{t('costLedger')} <b style={{ color: 'var(--color-hermes-cyan)' }}>{costLedger === null ? '--' : `$${costLedger.toFixed(4)}`}</b></span>}
    </div>
  )
}
