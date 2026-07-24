import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getEcoLink } from '../lib/endpoints'
import type { EcoLinkImpactPath } from '../lib/types'
import EcoLinkImpactPanel from '../components/EcoLinkImpactPanel'
import { ErrorState, LoadingState } from '../components/StatusStates'

/** 「EcoLink 影響路徑」獨立查詢頁（模組③ Wave 3）：查詢單一資產的官方
 * 升級事件與依賴邊之間*相關性*影響路徑，解耦於 `/compare`/`/analyze`——
 * 唯讀、不算費用、不觸發任何連接器。目前 fixture 只收錄少數 L2/L1
 * 資產（`asset:arb`/`asset:op`/`asset:matic`/`asset:eth`），其餘資產查詢
 * 會誠實回 `insufficient_data`，不是錯誤。 */

const SUGGESTIONS = ['asset:arb', 'asset:op', 'asset:matic', 'asset:eth', 'asset:sol', 'asset:bnb']

export default function EcoLinkPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialAsset = searchParams.get('asset') || 'asset:arb'
  const [input, setInput] = useState(initialAsset)
  const [asset, setAsset] = useState(initialAsset)
  const [verdict, setVerdict] = useState<'possible_relation' | 'insufficient_data' | null>(null)
  const [message, setMessage] = useState('')
  const [impactPaths, setImpactPaths] = useState<EcoLinkImpactPath[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)

  useEffect(() => {
    if (!asset.trim()) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getEcoLink(asset.trim(), controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        if (response.ok) {
          setVerdict(response.data.verdict)
          setMessage(response.data.message)
          setImpactPaths(response.data.impact_paths)
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
  }, [asset])

  function submitAsset(next: string) {
    const normalized = next.trim()
    if (!normalized) return
    setInput(normalized)
    if (normalized !== asset) {
      setVerdict(null)
      setMessage('')
      setImpactPaths([])
      setError(null)
      setLoading(true)
    }
    setAsset(normalized)
    setSearchParams({ asset: normalized })
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
      <header className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold text-tf-link">HERMES</p>
        <h1 className="mt-1 text-xl font-bold text-tf-text">EcoLink 影響路徑</h1>
        <p className="mt-2 text-sm text-tf-muted">
          查詢官方升級事件與依賴資產之間的可能相關性——不是已證實的因果關係。
        </p>
      </header>

      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-center"
        onSubmit={(e) => {
          e.preventDefault()
          submitAsset(input)
        }}
      >
        <label htmlFor="eco-link-asset" className="sr-only">
          資產識別碼
        </label>
        <input
          id="eco-link-asset"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="輸入資產識別碼，例如 asset:arb"
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
        {SUGGESTIONS.map((sug) => (
          <button
            key={sug}
            type="button"
            onClick={() => submitAsset(sug)}
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-xs transition ${
              sug === asset
                ? 'border-tf-link text-tf-link'
                : 'border-tf-border text-tf-muted hover:border-tf-link hover:text-tf-text'
            }`}
          >
            {sug}
          </button>
        ))}
      </div>

      {loading && <LoadingState label="查詢中…" />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && verdict && (
        <EcoLinkImpactPanel verdict={verdict} message={message} impactPaths={impactPaths} />
      )}
    </div>
  )
}
