import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getOverview } from '../lib/endpoints'
import type { OverviewCoin } from '../lib/types'
import OverviewCard from '../components/OverviewCard'
import { ErrorState, LoadingState } from '../components/StatusStates'

const HERO_HREF = '/analyze?coin=BTC&type=multi_source&q=' + encodeURIComponent('分析BTC近期市場狀況，整合多源資料')
const EXAMPLE_HREF =
  '/analyze?coin=BTC&type=multi_source&q=' +
  encodeURIComponent('分析該幣種近期市場狀況，整合多源資料') +
  '&sample=1'

const STEPS = [
  { badge: '步驟 1/3', title: '事實', body: '從多個獨立來源蒐集客觀資料——價格、鏈上、監管、新聞、社群。' },
  { badge: '步驟 2/3', title: '推論', body: 'Agent 交叉比對事實，標記反方訊號與可能推翻結論的條件。' },
  { badge: '步驟 3/3', title: '結論', body: '給出市場判斷，並附上校準後信心與完整證據可回溯。' },
]

export default function HomePage() {
  const [coins, setCoins] = useState<OverviewCoin[] | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // 卸載時真的 abort 底層 fetch（不只是忽略回應）：省下無謂的網路等待，
    // 也讓 apiClient 能區分「主動取消」與「逾時」兩種失敗來源。
    const controller = new AbortController()
    getOverview(controller.signal).then((res) => {
      // 已取消（卸載）——這個結果是被取代的舊請求，靜默捨棄，不當錯誤
      // UI、不覆蓋任何狀態。
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) setCoins(res.data.coins)
      else setError(res.error)
    })
    return () => {
      controller.abort()
    }
  }, [])

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-8 px-4 py-8 sm:px-6">
      <section
        className="rounded-xl border p-6 sm:p-10"
        style={{ borderColor: '#1f6feb', background: 'linear-gradient(135deg, rgba(31,111,235,.10), rgba(31,111,235,.02))' }}
      >
        <h1 className="text-2xl font-bold leading-snug text-tf-text sm:text-3xl">
          多源市場情報的信任提煉——不只給分數，給你為什麼
        </h1>
        <p className="mt-3 max-w-2xl text-sm text-tf-text2 sm:text-base">
          TrustForge 整合價格、鏈上、監管、新聞、社群多個獨立來源，逐項拆解信任來源，
          而不是丟一個黑箱分數給你。
        </p>
        <Link
          to={HERO_HREF}
          className="mt-5 inline-flex items-center gap-1 rounded-md bg-tf-accent px-4 py-2 text-sm font-semibold text-white no-underline hover:opacity-90"
        >
          立即開始分析 &#8594;
        </Link>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-tf-text">多幣信任總覽</h2>
        {loading && <LoadingState label="總覽載入中…" />}
        {!loading && error && <ErrorState code={error.code} message={error.message} />}
        {!loading && !error && coins && coins.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            {coins.map((c) => (
              <OverviewCard key={c.coin} coin={c} />
            ))}
          </div>
        )}
        {!loading && !error && coins && coins.length === 0 && (
          <p className="text-sm text-tf-muted">目前總覽資料尚未就緒，稍後再試。</p>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-tf-text">怎麼運作</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.badge} className="rounded-lg border border-tf-border bg-tf-card p-4">
              <span className="mb-2 inline-block rounded-full bg-tf-accent/20 px-2 py-0.5 text-[0.68rem] font-mono font-semibold text-tf-link">
                {s.badge}
              </span>
              <p className="text-sm font-semibold text-tf-text">{s.title}</p>
              <p className="mt-1 text-xs text-tf-muted">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <Link
          to={EXAMPLE_HREF}
          className="inline-flex items-center gap-1 rounded-md border border-tf-border px-4 py-2 text-sm font-semibold text-tf-text no-underline hover:border-tf-accent"
        >
          看範例報告 &#8594;
        </Link>
      </section>
    </main>
  )
}
