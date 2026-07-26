import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import CoinSelect from './CoinSelect'
import { useHermesI18n } from '../hermes/hermesI18n'

export interface QueryValues {
  coin: string
  type: string
  mode: string
  q: string
}

const ANALYSIS_MODES = [
  { value: 'risk', labelKey: 'qcModeRisk', type: 'multi_source' },
  { value: 'sentiment', labelKey: 'qcModeSentiment', type: 'multi_source' },
  { value: 'fundamentals', labelKey: 'qcModeFundamentals', type: 'hypothesis' },
  { value: 'news', labelKey: 'qcModeNews', type: 'multi_source' },
  { value: 'catalyst', labelKey: 'qcModeCatalyst', type: 'hypothesis' },
] as const

interface Props {
  initial: QueryValues
  onSubmit: (values: QueryValues) => void
  disabled?: boolean
}

// 單幣分析的預設問題文字——跟 `AnalyzePage.tsx::paramsFromSearch` 是同一份
// 字面字串（刻意不跨檔 export：避免這個元件檔案混雜 component 以外的 value
// export 而失去 react-refresh 資格，比照 `ComparePage.tsx::defaultQuery`
// 同樣選擇就地各自一份、非共用 import）。
function defaultAnalyzeQuery(coin: string): string {
  return `分析${coin}近期市場狀況，整合多源資料`
}

export default function QueryConsole({ initial, onSubmit, disabled = false }: Props) {
  const { t } = useHermesI18n()
  const [coin, setCoin] = useState(initial.coin)
  const [type, setType] = useState(initial.type)
  const [mode, setMode] = useState(initial.mode)
  const [q, setQ] = useState(initial.q)
  // 使用者是否手動編輯過問題欄——一旦編輯過，換幣就不再覆蓋掉他打的字；
  // 「送出/瀏覽器上下頁」會在下方 effect 裡重置，視為開啟一輪新的表單狀態。
  const [queryEdited, setQueryEdited] = useState(false)
  // 瀏覽器上下頁（或外部導覽帶新的 coin/type/q 進來）：URL 是唯一事實來源，
  // 把整個表單（含 queryEdited 旗標）重新對齊，避免可見控件跟網址/報告脫節。
  useEffect(() => {
    setCoin(initial.coin)
    setType(initial.type)
    setMode(initial.mode)
    setQ(initial.q)
    setQueryEdited(false)
  }, [initial.coin, initial.mode, initial.type, initial.q])

  function handleCoinChange(next: string) {
    setCoin(next)
    if (!queryEdited) setQ(defaultAnalyzeQuery(next))
  }

  return (
    <form
      className="hermes-clip flex flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (disabled) return
        onSubmit({ coin, type, mode, q })
      }}
    >
      <div className="border-b border-tf-border pb-3">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Run configuration</p>
        <h2 className="mt-1 text-sm font-semibold text-tf-text">{t('qcHeading')}</h2>
        <p className="mt-1 text-xs text-tf-muted">{t('qcSubtext')}</p>
      </div>
      <CoinSelect id="qc-coin" value={coin} onChange={handleCoinChange} />
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-type">
          {t('qcModeLabel')}
        </label>
        <select
          id="qc-type"
          value={mode}
          onChange={(e) => {
            const next = ANALYSIS_MODES.find((item) => item.value === e.target.value) ?? ANALYSIS_MODES[0]
            setMode(next.value)
            setType(next.type)
          }}
          className="w-full rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        >
          {ANALYSIS_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {t(mode.labelKey)}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[0.68rem] text-tf-muted">
          {t('qcComparePrefix')}
          <Link to="/compare" className="text-tf-link underline">
            {t('qcCompareLinkLabel')}
          </Link>
          {t('qcComparePeriod')}
        </p>
      </div>
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-q">
          {t('qcQuestionLabel')}
        </label>
        <textarea
          id="qc-q"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setQueryEdited(true)
          }}
          rows={3}
          className="w-full resize-none rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        />
      </div>
      <button
        type="submit"
        disabled={disabled}
        className="rounded-[8px] px-5 py-[13px] text-[13px] font-bold tracking-[0.06em] text-tf-bg hover:opacity-90"
        style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)', boxShadow: '0 0 20px rgba(77,216,224,0.25)' }}
      >
        {t('qcSubmit')} <span className="tf-num opacity-70">&#8594;</span>
      </button>
    </form>
  )
}
