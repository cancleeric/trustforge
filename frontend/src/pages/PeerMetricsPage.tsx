import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getPeerMetrics } from '../lib/endpoints'
import type { PeerComparisonEntry, PeerMetricsSnapshot } from '../lib/types'
import PeerComparisonTable from '../components/PeerComparisonTable'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useHermesI18n } from '../hermes/hermesI18n'

/** 「Peer 同層比較」獨立查詢頁（模組③ Wave 3）：查詢單一資產的同層
 * peer 比較 snapshot，解耦於 `/compare` 的雙幣分析比較流程——唯讀、
 * 不算費用、不觸發任何連接器。`/compare` 的幣種選單只涵蓋
 * `COIN_POOL`（L1 五幣），無法涵蓋 `asset:arb`/`asset:op`/`asset:matic`
 * 這類 L2 peer group，因此改用資產識別碼直接輸入（比照
 * `/asset-context`、`/eco-link` 的輸入模式），讓 L2 資產也查得到。 */

const SUGGESTIONS = ['asset:arb', 'asset:op', 'asset:matic', 'asset:eth', 'asset:sol', 'asset:bnb']

export default function PeerMetricsPage() {
  const { t } = useHermesI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialAsset = searchParams.get('asset') || 'asset:arb'
  const [input, setInput] = useState(initialAsset)
  const [asset, setAsset] = useState(initialAsset)
  const [snapshot, setSnapshot] = useState<PeerMetricsSnapshot | null>(null)
  const [peers, setPeers] = useState<PeerComparisonEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)

  useEffect(() => {
    if (!asset.trim()) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getPeerMetrics(asset.trim(), controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        if (response.ok) {
          setSnapshot(response.data.snapshot)
          setPeers(response.data.peers)
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
  }, [asset, t])

  function submitAsset(next: string) {
    const normalized = next.trim()
    if (!normalized) return
    setInput(normalized)
    if (normalized !== asset) {
      setSnapshot(null)
      setPeers([])
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
        <h1 className="mt-1 text-xl font-bold text-tf-text">{t('pmTitle')}</h1>
        <p className="mt-2 text-sm text-tf-muted">
          {t('pmDesc')}
        </p>
      </header>

      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-center"
        onSubmit={(e) => {
          e.preventDefault()
          submitAsset(input)
        }}
      >
        <label htmlFor="peer-metrics-asset" className="sr-only">
          {t('assetIdLabel')}
        </label>
        <input
          id="peer-metrics-asset"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={t('assetIdPlaceholder')}
          className="min-w-0 flex-1 rounded border border-tf-border bg-tf-bg px-3 py-2 font-mono text-sm text-tf-text outline-none focus:border-tf-link"
        />
        <button
          type="submit"
          className="rounded border border-tf-accent bg-tf-accent px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110"
        >
          {t('searchButton')}
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('quickSuggestionsAria')}>
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

      {loading && <LoadingState label={t('pmLoadingTemplate', { asset })} />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && snapshot && <PeerComparisonTable snapshot={snapshot} peers={peers} />}
      {!loading && !error && !snapshot && (
        <div className="rounded-lg border border-tf-border bg-tf-card p-6 text-center text-sm text-tf-muted">
          {t('pmEmptyStateTemplate', { asset })}
        </div>
      )}
    </div>
  )
}
