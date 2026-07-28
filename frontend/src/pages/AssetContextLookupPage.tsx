import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import BackToBridgeLink from '../components/BackToBridgeLink'
import { getAssetContext } from '../lib/endpoints'
import type { AssetContext } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import SectorLayerCard from '../components/SectorLayerCard'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useHermesI18n } from '../hermes/hermesI18n'

/** 「新手脈絡查詢」獨立小工具（模組①）：查詢單一資產的 sector/layer
 * 脈絡卡，解耦於 `/analyze` 的完整信任分析流程——不算費用、不觸發任何
 * 連接器，純讀 `data/asset_context_records.json` fixture。
 *
 * 2026-07-27 修正（CEO 回報「其他都空的，為什麼會有 ARB」）：原本 fixture
 * 裡只有 ARB 一筆，`SUGGESTIONS` 又把 ARB 排在最前面當預設，結果是——比賽
 * 指定的五幣全部查不到資料，畫面上唯一有內容的反而是不在比賽範圍的 ARB，
 * 主客完全顛倒。已為 COIN_POOL 五幣補上 L1 脈絡資料（settlement_chain /
 * gas_token / dependencies 都有值），預設改為 COIN_POOL[0]，先前的
 * 「L1/資料有限」標註也隨之退場（現在不再有限）。ARB 保留但排到最後並
 * 明示是範圍外的 L2 範例，避免再被誤認成第六種官方幣。 */

const SUGGESTIONS = [...COIN_POOL, 'ARB']

export default function AssetContextLookupPage() {
  const { t } = useHermesI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  // 預設幣種必須落在比賽指定的 COIN_POOL 內，不能是範圍外的 ARB。
  const initialSymbol = searchParams.get('symbol') || COIN_POOL[0]
  const [input, setInput] = useState(initialSymbol)
  const [symbol, setSymbol] = useState(initialSymbol)
  const [context, setContext] = useState<AssetContext | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)

  useEffect(() => {
    if (!symbol.trim()) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getAssetContext(symbol.trim().toUpperCase(), controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        if (response.ok) {
          setContext(response.data.asset_context)
        } else if (response.error.code !== 'cancelled') {
          setError(response.error)
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError({ code: 'network_error', message: t('networkErrorRetry') })
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [symbol, t])

  function submitSymbol(next: string) {
    const normalized = next.trim().toUpperCase()
    if (!normalized) return
    setInput(normalized)
    if (normalized !== symbol) {
      setContext(null)
      setError(null)
      setLoading(true)
    }
    setSymbol(normalized)
    setSearchParams({ symbol: normalized })
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
      <header className="flex flex-col gap-2 border-b border-tf-border pb-4">
        <BackToBridgeLink />
        <p className="font-mono text-xs font-semibold text-tf-link">{t('aclEyebrow')}</p>
        <h1 className="mt-1 text-xl font-bold text-tf-text">{t('aclTitle')}</h1>
        <p className="mt-2 text-sm text-tf-muted">
          {t('aclDesc')}
        </p>
      </header>

      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-center"
        onSubmit={(e) => {
          e.preventDefault()
          submitSymbol(input)
        }}
      >
        <label htmlFor="asset-context-symbol" className="sr-only">
          {t('assetSymbolLabel')}
        </label>
        <input
          id="asset-context-symbol"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={t('assetSymbolPlaceholder')}
          className="min-w-0 flex-1 rounded border border-tf-border bg-tf-bg px-3 py-2 font-mono text-sm text-tf-text outline-none focus:border-tf-link"
        />
        <button
          type="submit"
          className="rounded border border-tf-accent bg-tf-accent px-4 py-2 text-sm font-semibold text-tf-on-accent transition hover:brightness-110"
        >
          {t('searchButton')}
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('quickSuggestionsAria')}>
        {SUGGESTIONS.map((sym) => {
          const isOutOfScope = sym === 'ARB'
          return (
            <button
              key={sym}
              type="button"
              onClick={() => submitSymbol(sym)}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-xs transition ${
                sym === symbol.toUpperCase()
                  ? 'border-tf-link text-tf-link'
                  : 'border-tf-border text-tf-muted hover:border-tf-link hover:text-tf-text'
              }`}
            >
              {sym}
              {isOutOfScope && <span className="text-[0.65rem] text-tf-muted">{t('aclDemoOnly')}</span>}
            </button>
          )
        })}
      </div>

      {loading && <LoadingState label={t('searching')} />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && context && <SectorLayerCard context={context} />}
      {!loading && !error && !context && (
        <div className="rounded-lg border border-tf-border bg-tf-card p-6 text-center text-sm text-tf-muted">
          {t('aclEmptyState')}
        </div>
      )}
    </div>
  )
}
