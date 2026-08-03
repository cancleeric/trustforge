import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { TIER_COLOR } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'
import type { ServiceMonitorState } from '../pages/HermesDashboard'

const GIT_SHA = (import.meta.env.VITE_GIT_SHA || 'dev').slice(0, 7)

/** N70（CEO：「我們不要挑戰一般使用者」「能按的都移到左邊欄」「狀態要嘛放右欄
 *  要嘛放上方 做顯示 BAR 點了會打開」）：
 *
 *  頂欄原本混了兩種東西——**顯示**（品牌、系統代號、版號、連線狀態、成本帳本）
 *  與**操作**（5 條導覽鈕、完整模式、低動態、說明、艦體升級、語言）。一般使用者
 *  掃視時分不出哪些能按，等於整條列都要試一次。
 *
 *  N70 之後這裡只剩顯示，所有可按的控制項都搬到左軌（見 HermesLeftRail 的
 *  `hermes-rail-controls`）。唯一的例外是下面那顆「市場遙測」——它本身是狀態
 *  顯示，但內容有 5 個數值＋服務燈號，攤在左軌會把 HERMES 對話框往下擠
 *  （這正是 N46/N49 已經處理過一次的老問題），所以照 CEO 給的第二個選項做成
 *  「BAR 上的摘要，點了才展開」。
 *
 *  `runtimeStatus` 是上游 #793 AgentCore 整合新增的純顯示元件（AgentCoreStatusBadge），
 *  不是操作項，保留在頂欄。
 *
 *  ⚠️ 不要把按鈕加回這裡。要加操作項請加到左軌的控制區。 */

interface HermesTopBarProps {
  costLedger?: number | null
  version?: string
  systemId?: string
  degradedMessage?: string | null
  beginnerMode?: boolean
  runtimeStatus?: ReactNode
  /** 市場遙測（N70 從左軌搬上來，收在可展開的摘要膠囊裡）。 */
  trackedCount?: number
  tierCounts?: { healthy: number; moderate: number; danger: number }
  uplinkLatency?: string
  serviceMonitor?: Record<string, ServiceMonitorState>
}

export default function HermesTopBar({
  costLedger = null,
  version = '… · GALAXY', // 同 HermesDashboard：預設值不要長得像真的版號
  systemId = 'SYS·HRM-01',
  degradedMessage = null,
  beginnerMode = false,
  runtimeStatus,
  trackedCount = 0,
  tierCounts = { healthy: 0, moderate: 0, danger: 0 },
  uplinkLatency = '2.4s',
  serviceMonitor = {},
}: HermesTopBarProps) {
  const { t, locale, setLocale } = useHermesI18n()
  // 後端沒走發版流程時 /api/health 會回 version: "dev"。那不是版號，是「這台
  // 沒被建置過」的哨兵值；照原樣用一般樣式印出來，看起來就像系統版本叫 dev。
  // 標成警示色並掛說明，讓它讀起來是狀態而不是版本名。
  const isUnbuiltVersion = /^dev\b/.test(version)
  const [telemetryOpen, setTelemetryOpen] = useState(false)
  const telemetryRef = useRef<HTMLDivElement>(null)

  // 展開的面板是浮層，點面板外面要收起來——否則它會一直蓋住底下的全息區，
  // 而使用者第一直覺不是「再按一次那顆膠囊」。Esc 一併處理（鍵盤使用者
  // 沒有「點外面」這個動作）。
  useEffect(() => {
    if (!telemetryOpen) return
    function onPointerDown(e: MouseEvent) {
      if (!telemetryRef.current?.contains(e.target as Node)) setTelemetryOpen(false)
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setTelemetryOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [telemetryOpen])

  const row = { display: 'flex', justifyContent: 'space-between', fontSize: 11 } as const
  const rowLabel = { color: 'var(--color-hermes-tx2)' } as const

  return (
    <div
      className="hermes-topbar"
      style={{
        /* N74（CEO：「這疊到」）：頂欄 zIndex 原本是 10，而它自己就是一個
           stacking context——遙測面板寫 zIndex 30 也只是「在頂欄內部排第 30」，
           永遠贏不過同層的 `.hermes-module-deck`（z-index 18）。分析工作區一開，
           面板就被工作區標題壓在下面（實測 900/1024/1280/1440 四個寬度全中）。
           修法是抬頂欄本身，不是再加大面板的數字。32 > 18（工作區）
           且 < 49（drilldown 遮罩），所以 drilldown 打開時頂欄照樣被壓暗。 */
        position: 'absolute', left: 0, right: 0, top: 0, height: 'var(--hermes-top)', zIndex: 32,
        display: 'flex', alignItems: 'center', gap: 14, padding: '0 20px',
        background: 'rgba(10,16,24,.62)', backdropFilter: 'blur(10px)', willChange: 'backdrop-filter',
        borderBottom: '1px solid var(--color-hermes-bd)',
        boxShadow: '0 1px 12px rgba(77,216,224,.08)',
      }}
    >
      {/* N70：品牌從 <button onClick={onHome}> 改回純顯示。「能按的都移到左邊欄」
          包含這顆——回首頁的入口在左軌控制區第一項（HERMES 主控）。
          原本 N29 為它加的 24px 最小點擊目標隨按鈕一起移到左軌那顆。 */}
      {/* N80：加 `flexShrink: 0`。光把字級改成 clamp 不夠——實測 375 寬時這格
          反而從 115px 被壓到 100px、320 寬只剩 71px，因為它是頂欄 flex 裡預設
          可壓縮的一員。字級縮多少都沒用，容器一直在收。產品名是識別資訊，不該
          是被犧牲的那一個；要讓位的是右邊那些狀態格。 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexShrink: 0 }}>
        <div style={{ width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)', border: '1.5px solid var(--color-hermes-cyan)', borderRadius: 2 }}>
          <div style={{ position: 'absolute', inset: 3, background: 'var(--color-hermes-cyan)', opacity: 0.85 }} />
        </div>
        {/* N80：品牌字原本固定 13px + 1.6px 字距，需要 160px；375 寬的手機上
            這格只分到 115px，「TRUSTFORGE HERMES」被切掉 28%，畫面上是
            「TRUSTFORGE HERM…」。產品名被切一半比字小一點難看得多，所以字級
            與字距都改成隨視窗收斂，寬螢幕維持原本的 13px/1.6px 不變。 */}
        <span style={{ fontWeight: 700, fontSize: 'clamp(10px, 2.4vw, 13px)', letterSpacing: 'clamp(.3px, .35vw, 1.6px)', whiteSpace: 'nowrap', color: 'var(--color-hermes-tx)' }}>
          {/* N80：窄螢幕只留「HERMES」。375 英文版頂欄實測內容總寬 457px、
              可用只有 373px，一定要有東西讓位；品牌自己佔 149px 是最大的一格。
              把產品名切成「TRUSTFORGE HERM…」最糟，收掉前綴則是手機上常見且
              可讀的做法，而且不必砍掉任何狀態顯示。 */}
          <span className="hermes-brand-prefix">TRUSTFORGE </span>
          <span style={{ color: 'var(--color-hermes-cyan)' }}>HERMES</span>
        </span>
      </div>
      {/* 版號與系統代號原本吃 --color-hermes-tx3（#526375），在頂欄那層
          rgba(10,16,24,.62) 疊 #02040a 的底上實測只有 3.2:1，而且字級只有
          9~10px——CEO 直接回報「版號在哪裡？我怎麼畫面看不到」。改吃 tx2
          （#7f97ab，6.4:1）。這兩格是識別資訊，不是裝飾文字，不該用最淡的階。 */}
      {/* N80：加 class 讓窄螢幕收掉這一格。品牌不再被壓縮之後，頂欄在英文版
          375~561 寬會把最右邊的語言切換鈕擠出視窗（實測 375 en 溢出 82px）。
          總寬度不夠時一定要有人讓位——讓位的該是系統代號：CEO 當初要求看得到的
          是「版號」，代號只是識別用的裝飾資訊，收掉不影響任何操作。 */}
      <span className="hermes-topbar-sysid" style={{ fontSize: 9, color: 'var(--color-hermes-tx2)', letterSpacing: 1 }}>✛ {systemId}</span>
      <span
        title={isUnbuiltVersion ? t('versionDevHint') : undefined}
        style={{ fontSize: 10, color: isUnbuiltVersion ? 'var(--color-hermes-amber)' : 'var(--color-hermes-tx2)', border: `1px solid ${isUnbuiltVersion ? 'rgba(232,179,77,.4)' : 'var(--color-hermes-bd2)'}`, borderRadius: 4, padding: '2px 7px' }}
      >{`${version} · ${GIT_SHA}`}</span>
      <span className="hermes-uplink-status" title={degradedMessage || undefined} aria-label={degradedMessage ? t('degradedState') : t('liveUplink')} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', background: degradedMessage ? 'rgba(232,179,77,.13)' : 'rgba(77,216,224,.13)', border: `1px solid ${degradedMessage ? 'rgba(232,179,77,.4)' : 'rgba(77,216,224,.4)'}`, borderRadius: 4, padding: '2px 8px' }}>
        <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', animation: 'hermes-pulse 1.8s infinite' }} />
        {/* N28: on very narrow phones (≤430px) the topbar has no room left
            after logo + toggles for this label text without pushing trailing
            controls off-screen. The parent span keeps its aria-label/title
            with the full text, so this is a visual-only collapse, not a loss
            of information. */}
        <span className="hermes-uplink-status-label" aria-hidden="true">{degradedMessage ? t('degradedState') : t('liveUplink')}</span>
      </span>

      {/* N70 市場遙測：摘要膠囊 + 點擊展開的面板。摘要只放兩個數字（追蹤、
           需要注意的總數），因為頂欄的預算是一行；細節在面板裡。 */}
      <div ref={telemetryRef} style={{ position: 'relative' }}>
        <button
          type="button"
          className="hermes-telemetry-chip"
          onClick={() => setTelemetryOpen((v) => !v)}
          aria-expanded={telemetryOpen}
          aria-controls="hermes-telemetry-panel"
          /* N80：窄螢幕會把標題文字收掉（見下方 span），所以名字改由 aria-label 提供，
             否則螢幕閱讀器只會念到兩個數字。 */
          aria-label={t('telemetry')}
          style={{
            display: 'flex', alignItems: 'center', gap: 7, minHeight: 24, cursor: 'pointer',
            fontFamily: 'inherit', fontSize: 10, color: 'var(--color-hermes-tx2)',
            background: 'transparent', border: '1px solid var(--color-hermes-bd2)',
            borderRadius: 4, padding: '2px 8px',
          }}
        >
          {/* N80：窄螢幕收掉標題文字，只留數字。實測 375 英文版頂欄內容 376px、
              可用 373px，語言切換鈕被擠出視窗；這一格「GALAXY TELEMETRY」佔 125px，
              是頂欄最大的一塊。
              先試過縮 padding / gap，量出來反而更寬（pad12 → 378、gap10 → 384）：
              這一格是可伸縮的 flex 子元素，空間讓出來就被它吃掉，所以刪空白沒有用，
              必須讓內容自己變短。收掉之後 scrollWidth 373 = 可用寬，剛好進得去。
              膠囊本身（可點、會展開面板）完整保留，符合「能按的不能藏」；名字改掛
              aria-label。 */}
          <span className="hermes-telemetry-chip-label" style={{ letterSpacing: 1 }}>{t('telemetry')}</span>
          <span style={{ color: 'var(--color-hermes-tx)' }}>{trackedCount}</span>
          {/* 注意/警示不為 0 時才上色——全綠的時候不需要吸引注意力。 */}
          {tierCounts.moderate > 0 && <span style={{ color: TIER_COLOR.moderate }}>▲{tierCounts.moderate}</span>}
          {tierCounts.danger > 0 && <span style={{ color: TIER_COLOR.danger }}>●{tierCounts.danger}</span>}
          <span aria-hidden="true" style={{ color: 'var(--color-hermes-tx3)' }}>{telemetryOpen ? '▲' : '▼'}</span>
        </button>
        {telemetryOpen && (
          <div
            id="hermes-telemetry-panel"
            role="group"
            aria-label={t('telemetry')}
            style={{
              /* 面板靠膠囊的**右**緣展開，不是左緣：膠囊在頂欄中段，靠左展開時
                 210px 會直接掉出畫面右緣（實測 320~900px 全部溢出，768 溢出
                 83px）。右對齊 + max-width 夾住視窗寬度，任何寬度都在畫面內。 */
              position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 30,
              width: 210, maxWidth: 'calc(100vw - 20px)',
              display: 'flex', flexDirection: 'column', gap: 7,
              background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)',
              borderRadius: 6, padding: '10px 12px', boxShadow: '0 8px 24px rgba(0,0,0,.45)',
            }}
          >
            <div style={row}><span style={rowLabel}>{t('tracked')}</span><span style={{ color: 'var(--color-hermes-tx)' }}>{trackedCount}</span></div>
            <div style={row}><span style={rowLabel}>{t('healthy')}</span><span style={{ color: TIER_COLOR.healthy }}>{tierCounts.healthy}</span></div>
            <div style={row}><span style={rowLabel}>{t('moderate')}</span><span style={{ color: TIER_COLOR.moderate }}>{tierCounts.moderate}</span></div>
            <div style={row}><span style={rowLabel}>{t('danger')}</span><span style={{ color: TIER_COLOR.danger }}>{tierCounts.danger}</span></div>
            <div style={row}><span style={rowLabel}>{t('latency')}</span><span style={{ color: 'var(--color-hermes-tx)' }}>{uplinkLatency}</span></div>
            <div className="hermes-service-monitor" aria-label="system link monitor">
              {Object.entries(serviceMonitor).map(([name, state]) => {
                const label = state === 'ok' ? 'UP' : state === 'empty' ? 'NO DATA' : state === 'stale' ? 'DEGRADED' : state === 'error' ? 'DOWN' : 'CHECK'
                return <span key={name} className={`is-${state}`} title={`${name}: ${label}`}><i />{name} · {label}</span>
              })}
            </div>
          </div>
        )}
      </div>

      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--color-hermes-amber)', background: 'rgba(232,179,77,.13)', border: '1px solid rgba(232,179,77,.4)', borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, transform: 'rotate(45deg)', background: 'var(--color-hermes-amber)', animation: 'hermes-pulse 2.4s infinite' }} />{t('systemActivePrefix')} {t('active')}
      </span>
      <div style={{ flex: 1 }} />
      {runtimeStatus}
      {!beginnerMode && <span style={{ fontSize: 10, color: 'var(--color-hermes-tx2)' }}>{t('costLedger')} <b style={{ color: 'var(--color-hermes-cyan)' }}>{costLedger === null ? '--' : `$${costLedger.toFixed(4)}`}</b></span>}
      {/* N72（CEO：「中文 英文 放右上，不要拿到左邊很怪」）：語言切換是全域
          偏好、慣例位置就在右上角，放進左軌會被讀成「跟分析同一層的功能」。
          這是 N70「頂欄只留狀態」的**明示例外**，由 CEO 指定，別再搬回左軌。 */}
      <button type="button" className="hermes-topbar-lang" aria-label={t('language')}
        onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')}>
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
    </div>
  )
}
