import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'
import { getHealth } from '../lib/endpoints'
import { useHermesI18n } from '../hermes/hermesI18n'

// build 時由 CD workflow 注入（VITE_GIT_SHA，見 .github/workflows/deploy-frontend.yml），
// 讓「線上 bundle 對應哪個 commit」可在畫面上直接確認；本機開發未設時 fallback 'dev'。
const GIT_SHA = (import.meta.env.VITE_GIT_SHA || 'dev').slice(0, 7)
const BUILD_VERSION = import.meta.env.VITE_RELEASE_VERSION || 'build'

export default function Header() {
  const [releaseVersion, setReleaseVersion] = useState(BUILD_VERSION)
  const { locale, setLocale, t } = useHermesI18n()
  // 'build' = 沒注入 VITE_RELEASE_VERSION；'dev' = 後端沒走發版流程。兩者都是
  // 哨兵值而非版號，見下方版號徽章的註解。
  const isUnversioned = releaseVersion === 'build' || /^dev\b/.test(releaseVersion)
  const navItems = [
    { to: '/', label: 'HERMES' }, { to: '/analyze', label: t('analyze') },
    { to: '/compare', label: t('compare') }, { to: '/history', label: t('history') },
    { to: '/status', label: t('sources') }, { to: '/costs', label: t('costs') },
  ]

  useEffect(() => {
    const controller = new AbortController()
    void getHealth(controller.signal).then((response) => {
      if (response.ok) setReleaseVersion(response.data.version)
    }).catch(() => {
      // Keep the build-time value visible if the health endpoint is briefly unavailable.
    })
    return () => controller.abort()
  }, [])

  return (
    <header
      className="app-header flex min-h-[52px] flex-wrap items-stretch gap-x-4 gap-y-2 border-b border-tf-border bg-tf-card px-4 py-2 sm:px-6"
    >
      {/* N35: 實測 194x20，寬度夠但高度只有 20px，低於 24px 最小點擊目標。 */}
      <Link to="/" className="inline-flex min-h-[24px] items-center gap-2 self-center no-underline">
        <span
          style={{
            width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)',
            border: '1.5px solid var(--color-tf-link)', borderRadius: 2, display: 'inline-block',
          }}
        >
          <span style={{ position: 'absolute', inset: 3, background: 'var(--color-tf-link)', opacity: 0.85 }} />
        </span>
        <span className="font-mono text-sm font-bold tracking-[1.6px] text-tf-text">
          TRUSTFORGE <span style={{ color: 'var(--color-tf-link)' }}>HERMES</span>
        </span>
      </Link>

      {/* N35: 這支 nav 原本是 header 裡唯一帶 `min-w-0 flex-1` 的子項，於是成為
          唯一的收縮受害者——logo、版號徽章、EN 鈕、4 條次要連結、主題切換都不縮，
          擠到 320–768px 時 nav 只剩 30–63px 可視寬（內容需 339px），6 條主導航有 5
          條看不到，且 `overflow-x-auto` 在此寬度不長 scrollbar（實測 scrollbar=0），
          使用者根本不知道有導航可滾。改成 xl 以下 `w-full` 自成一列（flex-wrap 下
          100% 寬無法與人共行），寬螢幕才回到原本的 flex-1 行為。 */}
      {/* 320px 下 6 條連結共需 339px，只靠 overflow-x-auto 的話最後一條「成本」落在
          x=355（實測點不到，得先橫滾才知道它存在）。窄寬度改成可換行，讓 6 條全部
          直接可見；xl（1280）以上才維持單列 nowrap。斷點取 xl 而非 lg：lg=1024 時
          英文標籤在 1024x420 實測仍被擠到點不到。 */}
      <nav aria-label={t('mainNav')} className="flex w-full min-w-0 flex-wrap items-center gap-1 xl:w-auto xl:flex-1 xl:flex-nowrap xl:items-stretch xl:overflow-x-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              // h-full 只在 xl 以上生效：換行模式下每個 link 都撐 h-full 會讓 nav 盒子
              // 停在單列高度（實測 52px），第二列整排溢出到盒外被版號徽章蓋住點不到。
              `relative flex min-h-[24px] items-center whitespace-nowrap rounded px-2 py-1 font-mono text-xs uppercase tracking-wider no-underline transition xl:h-full xl:py-0 ${
                isActive ? 'font-semibold text-tf-link' : 'text-tf-muted hover:text-tf-text'
              }`
            }
            style={({ isActive }) => (isActive ? { textShadow: '0 0 8px rgba(77,216,224,.45)' } : {})}
          >
            {({ isActive }) => (
              <>
                {item.label}
                {/* 錨在 nav 的底部（自身 h-full 撐滿 header 高度），不是靠
                    padding-bottom 撐出間距——設計稿要求的 5 個核心頁籤才有
                    這條線，Admin/Settings 不在 navItems 裡故天生沒有。 */}
                {isActive && <span className="absolute inset-x-1 bottom-0 h-[2px] bg-tf-link" aria-hidden="true" />}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* 跟 HermesTopBar 同一個問題：`build`（未注入 VITE_RELEASE_VERSION）與
          `dev`（後端沒走發版流程時 /api/health 的回值）都是哨兵值，不是版號。
          用一般樣式印成「build · dev」會被讀成版本名叫 build。標成 amber 並
          換一段說明文案，讓它讀起來是「這份 bundle 沒有版本資訊」的狀態。 */}
      <span
        title={isUnversioned ? t('hdrUnversionedTitle') : t('hdrDeployVersionTitle')}
        className={`self-center rounded border px-2 py-0.5 font-mono text-xs ${
          isUnversioned ? 'border-tf-warn/50 text-tf-warn' : 'border-tf-muted/40 text-tf-muted'
        }`}
      >{`${releaseVersion} · ${GIT_SHA}`}</span>
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} className="self-center rounded border border-tf-border bg-transparent px-2 py-1 font-mono text-xs text-tf-muted hover:text-tf-text">
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      <Link to="/asset-context" className="flex min-h-[24px] items-center self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        {t('hdrAssetContext')}
      </Link>
      {/* ⛔ N68：EcoLink（/eco-link）刻意不掛在主導覽——請不要「順手加回來」。
          原因是結構性的、不是資料還沒補：`src/trustforge/ecolink.py` 的
          `OFFICIAL_ECOLINK_HOSTS` 只放行 arbitrum.foundation / blog.arbitrum.io /
          forum.arbitrum.foundation / gov.optimism.io / ethereum.org 五個官方網域，
          來源不在名單內的 fixture 一律被 `_ensure_allowlisted_host` 擋掉。
          也就是說 EcoLink 依設計只能涵蓋 ETH L2 生態，對比賽指定的 COIN_POOL
          （BTC/ETH/SOL/BNB/XRP —— 五條互不相依的 L1）永遠給不出路徑，
          點進去幾乎必定撲空。CEO 實測回報「只有 ARB 有東西 其他也是空的」。
          要恢復這個連結的前提是先擴充 allowlist 並補進真實的官方依賴邊；
          在那之前把它擺在跟「資產脈絡查詢」平起平坐的位置就是在誤導使用者。
          ⚠️ 不接受的「修法」：替五幣捏造依賴邊讓畫面有東西——`ImpactPath`
          的 docstring 明訂路徑只是「可能相關」而非因果，造資料正是它禁止的事。
          路由本身保留在 App.tsx，深連結、既有測試、內部示範都還能用。
          `EcoLinkPage.test.tsx` 有一條測試把 chip 綁死在 fixture 真的收錄的資產。 */}
      {/* N68：空出來的位子換成 /settings。那頁本來就存在、雙語齊全、主題切換
          是真的串 lib/theme.ts，但全站沒有任何連結指向它——只能手打網址。
          用一個「有路由沒入口」的可用頁，換掉一個「有入口沒資料」的死路。 */}
      <Link to="/settings" className="flex min-h-[24px] items-center self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        {t('settings')}
      </Link>
      <Link to="/peer-metrics" className="flex min-h-[24px] items-center self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        {t('hdrPeerCompare')}
      </Link>
      <Link to="/help" className="flex min-h-[24px] items-center self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        ? {t('help')}
      </Link>
      <span className="self-center">
        <ThemeToggle />
      </span>
    </header>
  )
}
