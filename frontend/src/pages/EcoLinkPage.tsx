import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import BackToBridgeLink from '../components/BackToBridgeLink'
import { getEcoLink } from '../lib/endpoints'
import type { EcoLinkImpactPath } from '../lib/types'
import EcoLinkImpactPanel from '../components/EcoLinkImpactPanel'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useHermesI18n } from '../hermes/hermesI18n'

/** 「EcoLink 影響路徑」獨立查詢頁（模組③ Wave 3）：查詢單一資產的官方
 * 升級事件與依賴邊之間*相關性*影響路徑，解耦於 `/compare`/`/analyze`——
 * 唯讀、不算費用、不觸發任何連接器。
 *
 * N67（CEO 回報「只有 ARB 有東西 其他也是空的」）：原本六個 chip 只有
 * `asset:arb` 查得到路徑，其餘五個一律回 `insufficient_data`，畫面看起來
 * 就是壞的。追下去發現不是 fixture 沒補齊，是模組的先天範圍——
 * `ecolink.OFFICIAL_ECOLINK_HOSTS` 只放行 arbitrum / optimism / ethereum
 * 三家官方網域，所以 EcoLink 依設計就只能涵蓋 ETH L2 生態；
 * `asset:sol` / `asset:bnb` 永遠不可能有合法來源的資料，掛在這裡是死路。
 * 新加的測試又抓出兩個我一開始漏掉的：`asset:matic` 只出現在
 * `impacted_asset_ids`、`asset:eth` 只是邊的另一端，兩者都不是任何升級事件
 * 的主體，`impact_paths_for` 查下去一樣是空的。六個 chip 實際只有兩個活的。
 *
 * ⚠️ 不補造假資料：本模組的 docstring 明訂路徑只能是「可能相關」，
 * 為了讓畫面有東西而替官方五幣捏造依賴邊，正好是它禁止的事。
 * 改成誠實呈現：chip 只留真的可能有資料的資產並標明是 ETH L2 示範、
 * 空狀態說明「為什麼」空（沒有收錄的官方依賴邊，或 confidence 低於門檻），
 * 而不是丟一句「資料不足」讓人以為是壞掉。
 *
 * `asset:op` 刻意保留：它有依賴邊但 confidence 0.35 低於
 * `DEFAULT_MIN_CONFIDENCE = 0.4`，是「門檻確實有在擋」的活證據。 */

const SUGGESTIONS = ['asset:arb', 'asset:op']

export default function EcoLinkPage() {
  const { t } = useHermesI18n()
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
      <header className="flex flex-col gap-2 border-b border-tf-border pb-4">
        <BackToBridgeLink />
        <p className="font-mono text-xs font-semibold text-tf-link">HERMES</p>
        <h1 className="mt-1 text-xl font-bold text-tf-text">{t('elTitle')}</h1>
        <p className="mt-2 text-sm text-tf-muted">
          {t('elDesc')}
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
          {t('assetIdLabel')}
        </label>
        <input
          id="eco-link-asset"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={t('assetIdPlaceholder')}
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
      {/* N67：明講這幾個 chip 是 ETH L2 示範資產、不是比賽指定的五幣，
          免得再被誤讀成「我們自己多了一種幣」。 */}
      <p className="-mt-3 text-xs text-tf-muted">{t('elScopeNote')}</p>

      {loading && <LoadingState label={t('searching')} />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && verdict && (
        <EcoLinkImpactPanel verdict={verdict} message={message} impactPaths={impactPaths} />
      )}
    </div>
  )
}
