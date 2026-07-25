import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAssetContext } from '../lib/endpoints'
import type { AssetContext } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import SectorLayerCard from '../components/SectorLayerCard'
import { ErrorState, LoadingState } from '../components/StatusStates'

/** 「新手脈絡查詢」獨立小工具（模組①）：查詢單一資產的 sector/layer
 * 脈絡卡，解耦於 `/analyze` 的完整信任分析流程——不算費用、不觸發任何
 * 連接器，純讀 `data/asset_context_records.json` fixture。目前只有 ARB
 * 有完整 L2 脈絡資料；`COIN_POOL` 五幣本身是 L1，脈絡資料有限（無
 * settlement_chain/gas_token/dependencies 這類 L2 特有欄位），仍可查但
 * 標註「L1/資料有限」，不誤導成跟 ARB 同等豐富。 */

const SUGGESTIONS = ['ARB', ...COIN_POOL]

export default function AssetContextLookupPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialSymbol = searchParams.get('symbol') || 'ARB'
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
          setError({ code: 'network_error', message: '連線異常，請稍後再試' })
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [symbol])

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
      <header className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold text-tf-link">HERMES · 資產脈絡查詢</p>
        <h1 className="mt-1 text-xl font-bold text-tf-text">30 秒看懂一個代幣的定位</h1>
        <p className="mt-2 text-sm text-tf-muted">
          查詢資產的分類脈絡（Layer / 結算鏈 / Gas 代幣 / 上下游依賴），不觸發完整信任分析。
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
          資產代號
        </label>
        <input
          id="asset-context-symbol"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="輸入資產代號，例如 ARB"
          className="min-w-0 flex-1 rounded border border-tf-border bg-tf-bg px-3 py-2 font-mono text-sm text-tf-text outline-none focus:border-tf-link"
        />
        <button
          type="submit"
          className="rounded border border-tf-accent bg-tf-accent px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110"
        >
          查詢
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="快速查詢建議">
        {SUGGESTIONS.map((sym) => {
          const isArb = sym === 'ARB'
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
              {!isArb && <span className="text-[0.65rem] text-tf-muted">L1/資料有限</span>}
            </button>
          )
        })}
      </div>

      {loading && <LoadingState label="查詢中…" />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && context && <SectorLayerCard context={context} />}
      {!loading && !error && !context && (
        <div className="rounded-lg border border-tf-border bg-tf-card p-6 text-center text-sm text-tf-muted">
          目前無此資產的脈絡資料。
        </div>
      )}
    </div>
  )
}
