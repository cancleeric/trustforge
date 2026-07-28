import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getOverview, getStatus } from '../lib/endpoints'
import type { OverviewCoin, StatusData } from '../lib/types'
import { computeCompetitionRanks, sortCoinsByTrustScoreDesc } from '../lib/sortCoins'
import OverviewCard from '../components/OverviewCard'
import { ErrorState, LoadingState } from '../components/StatusStates'

const HERO_HREF = '/analyze?coin=BTC&type=multi_source&q=' + encodeURIComponent('分析BTC近期市場狀況，整合多源資料')
function SourceHealth({ status }: { status: StatusData | null }) {
  if (!status) return <span className="text-xs text-tf-muted">來源狀態載入中</span>
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span className="text-tf-good"><span className="tf-num font-semibold">{status.freshness.fresh}</span> 新鮮</span>
      <span className="text-tf-warn"><span className="tf-num font-semibold">{status.freshness.stale}</span> 過期</span>
      <span className="text-tf-muted"><span className="tf-num font-semibold">{status.freshness.missing}</span> 缺席</span>
    </div>
  )
}

export default function HomePage() {
  const [coins, setCoins] = useState<OverviewCoin[] | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState<StatusData | null>(null)

  useEffect(() => {
    // 卸載時真的 abort 底層 fetch（不只是忽略回應）：省下無謂的網路等待，
    // 也讓 apiClient 能區分「主動取消」與「逾時」兩種失敗來源。
    const controller = new AbortController()
    getOverview(controller.signal).then((res) => {
      // 已取消（卸載）——這個結果是被取代的舊請求，靜默捨棄，不當錯誤
      // UI、不覆蓋任何狀態。
      if (controller.signal.aborted) return
      setLoading(false)
      // #86：跨幣信任排行——依 `trust_score` 降序排列，純陣列排序，不推導
      // 後端未提供的欄位（每幣的 trust_score 已是後端算好的真值，這裡只是
      // 排序展示順序）。排序邏輯（含平手行為）抽到 `sortCoinsByTrustScoreDesc`
      // 純函式，見該檔 docstring 與 `sortCoins.test.ts`。
      if (res.ok) setCoins(sortCoinsByTrustScoreDesc(res.data.coins))
      else setError(res.error)
    })
    return () => {
      controller.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void getStatus(controller.signal).then((res) => {
      if (!controller.signal.aborted && res.ok) setStatus(res.data)
    })
    return () => controller.abort()
  }, [])

  // codex 窮舉終審 LOW 修復：平手用 competition ranking（1224 制），見
  // `computeCompetitionRanks()` docstring；`coins` 已經是
  // `sortCoinsByTrustScoreDesc()` 排序後的降序陣列，符合該函式前提。
  const competitionRanks = coins ? computeCompetitionRanks(coins) : []

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <section className="border-b border-tf-border pb-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs font-semibold uppercase text-tf-link">Hermes market desk</p>
            <h1 className="mt-1 text-2xl font-bold text-tf-text">市場快照</h1>
            <p className="mt-1 text-sm text-tf-text2">固定五幣的最新可稽核快照；點選任一幣種查看來源、推理與證據。</p>
          </div>
          <Link
            to={HERO_HREF}
            className="inline-flex items-center gap-1 rounded-md bg-tf-accent px-3 py-2 text-sm font-semibold text-tf-on-accent no-underline hover:opacity-90"
          >
            新增分析 &#8594;
          </Link>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-l-2 border-tf-accent bg-tf-card px-3 py-2.5">
          <div>
            <p className="text-xs font-semibold text-tf-text">來源快取健康度</p>
            <p className="mt-0.5 text-xs text-tf-muted">以最近一次已封存 snapshot 計算；缺席不會被當成中性資料。</p>
          </div>
          <SourceHealth status={status} />
        </div>
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-tf-text">多幣總覽</h2>
            <p className="mt-0.5 text-xs text-tf-muted">依信任分排序；資訊完整度反映本次可用、可交叉驗證資料的充分程度。</p>
          </div>
          <Link to="/status" className="text-xs font-semibold text-tf-link no-underline hover:underline">查看所有來源狀態 &#8594;</Link>
        </div>
        {loading && <LoadingState label="總覽載入中…" />}
        {!loading && error && <ErrorState code={error.code} message={error.message} />}
        {!loading && !error && coins && coins.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            {/* codex 窮舉終審 LOW 修復：平手用 competition ranking（1224
                制），不是陣列 index + 1，見 `computeCompetitionRanks()`
                docstring。*/}
            {coins.map((c, i) => (
              <OverviewCard key={c.coin} coin={c} rank={competitionRanks[i]} />
            ))}
          </div>
        )}
        {!loading && !error && coins && coins.length === 0 && (
          <p className="text-sm text-tf-muted">目前總覽資料尚未就緒，稍後再試。</p>
        )}
      </section>

      <p className="border-t border-tf-border pt-4 text-xs text-tf-muted">
        Hermes 工作流：資料快照 → 來源驗證 → 信任推理 → 證據綁定 → 可回溯報告
      </p>
    </main>
  )
}
