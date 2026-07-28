import { useHermesI18n } from './hermesI18n'

/** #361 首屏 hero tagline —— 讓評審在 5 秒內讀到 TrustForge 與一般 RAG／
 * Messari 機器人的差異：多源裁判、附證據／血統／反方意見。刻意放在
 * topbar 下方一條纖薄、永遠可見的 strip；不依附新手模式、不遮蔽中央
 * 星系視覺（pointerEvents:none，點擊穿透到星球）。 */
export default function HermesHeroTagline() {
  const { t } = useHermesI18n()
  return (
    <div
      className="hermes-hero-tagline"
      role="region"
      aria-label={t('tagline')}
      style={{
        // N38: 原本吃 `var(--hermes-top)`，但那個變數的語意是「內容從多下面
        // 開始」，而內容起點必須把這條 strip 本身算進去——同吃一個變數就等於
        // strip 永遠疊在內容第一排上。改吃 `--hermes-topbar`（topbar 自身高度），
        // 讓 strip 貼在 topbar 下緣，內容則由 --hermes-top 讓到 strip 下方。
        position: 'absolute', left: 0, right: 0, top: 'var(--hermes-topbar)', zIndex: 9,
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center',
        gap: 10, padding: '7px 20px',
        background: 'rgba(10,16,24,.42)', backdropFilter: 'blur(6px)', willChange: 'backdrop-filter',
        borderBottom: '1px solid var(--color-hermes-bd)',
        pointerEvents: 'none',
      }}
    >
      <span
        className="hermes-hero-badge"
        style={{
          flexShrink: 0, fontSize: 9, fontWeight: 700, letterSpacing: '.8px', whiteSpace: 'nowrap',
          color: 'var(--color-hermes-cyan)', background: 'rgba(77,216,224,.12)',
          border: '1px solid rgba(77,216,224,.4)', borderRadius: 4, padding: '2px 8px',
        }}
      >
        {t('badge')}
      </span>
      <span
        className="hermes-hero-tagline-text"
        style={{
          fontSize: 12, lineHeight: 1.4, letterSpacing: '.3px', textAlign: 'center',
          color: 'var(--color-hermes-tx)', maxWidth: 860,
        }}
      >
        {t('tagline')}
      </span>
    </div>
  )
}
