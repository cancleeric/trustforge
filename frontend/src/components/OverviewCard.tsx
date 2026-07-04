import { Link } from 'react-router-dom'
import type { OverviewCoin } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { DecisionStateBadge, DirectionBadge } from './Badges'

interface Props {
  coin: OverviewCoin
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

export default function OverviewCard({ coin }: Props) {
  const href = overviewCardHref(coin.coin)
  const body = (
    <div className="flex h-full flex-col gap-2 rounded-lg border border-tf-border bg-tf-card p-4 transition hover:border-tf-accent hover:shadow-[0_4px_14px_color-mix(in_srgb,var(--color-tf-accent)_18%,transparent)]">
      <div className="flex items-center justify-between">
        <span className="text-base font-bold text-tf-text">{coin.coin}</span>
        <DirectionBadge direction={coin.direction} />
      </div>
      <div className="tf-num flex items-baseline gap-1">
        <span className="text-2xl font-bold text-tf-text">{coin.trust_score.toFixed(2)}</span>
        <span className="text-xs text-tf-muted">信任分</span>
      </div>
      <p className="tf-num text-xs text-tf-muted">校準信心 {coin.calibrated_confidence.toFixed(2)}</p>
      <DecisionStateBadge state={coin.decision_state} />
    </div>
  )

  if (!href) return body
  return (
    <Link to={href} className="block no-underline focus-visible:outline-none">
      {body}
    </Link>
  )
}
