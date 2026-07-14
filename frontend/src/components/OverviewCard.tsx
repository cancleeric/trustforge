import { Link } from 'react-router-dom'
import { normalizeDecisionState, type OverviewCoin } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { DecisionStateBadge, DirectionBadge, ManipRiskBadge } from './Badges'
import { formatEpoch } from '../lib/format'

interface Props {
  coin: OverviewCoin
  /** #86：跨幣信任排行名次（1-based，依 `trust_score` 降序，由呼叫端
   * `HomePage` 排序後傳入）。選填——非排行情境（若有）沿用純 grid 顯示。 */
  rank?: number
}

/** 對應後端 `scripts/fetch_scheduler.py::_overview_card_href`：真 `/analyze`
 * 連結（真資料，不帶 `sample=1`），查詢文案沿用同一句 date-agnostic 句型。
 * 只對 `COIN_POOL` 白名單內的幣種組連結——非白名單一律不可點（防呆）。
 */
function overviewCardHref(coin: string): string | null {
  if (!(COIN_POOL as readonly string[]).includes(coin)) return null
  const params = new URLSearchParams({
    coin,
    type: 'multi_source',
    q: `分析${coin}近期市場狀況，整合多源資料`,
  })
  return `/analyze?${params.toString()}`
}

export default function OverviewCard({ coin, rank }: Props) {
  const href = overviewCardHref(coin.coin)
  // 每張卡固定並列兩個具名數值，不再依狀態互換主角；跨幣掃讀時相同位置
  // 永遠是相同指標。legacy 值仍先正規化為 normal。
  const decisionState = normalizeDecisionState(coin.decision_state)
  const body = (
    <div className="hermes-clip flex h-full flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4 transition hover:border-tf-accent hover:shadow-[0_4px_14px_color-mix(in_srgb,var(--color-tf-accent)_18%,transparent)]">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5">
          {rank !== undefined && (
            <span className="tf-num text-xs font-semibold text-tf-muted">#{rank}</span>
          )}
          <span className="text-base font-bold text-tf-text">{coin.coin}</span>
        </span>
        <DirectionBadge direction={coin.direction} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="border-l-2 border-tf-link pl-2">
          <p className="text-[0.68rem] font-semibold text-tf-muted">信任分</p>
          <p className="tf-num mt-0.5 text-2xl font-bold text-tf-text">{coin.trust_score.toFixed(2)}</p>
        </div>
        <div className="border-l-2 border-tf-good pl-2">
          <p className="text-[0.68rem] font-semibold text-tf-muted">資訊完整度</p>
          <p className="tf-num mt-0.5 text-2xl font-bold text-tf-text">{coin.calibrated_confidence.toFixed(2)}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <DecisionStateBadge state={decisionState} />
        <ManipRiskBadge manipScore={coin.manip_score} manipScoreMean={coin.manip_score_mean} />
      </div>
      <p className="tf-num mt-auto text-[0.68rem] text-tf-muted">快照 {formatEpoch(coin.fetched_at_epoch)}</p>
    </div>
  )

  if (!href) return body
  return (
    <Link to={href} className="block no-underline focus-visible:outline-none">
      {body}
    </Link>
  )
}
