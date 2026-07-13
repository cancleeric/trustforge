import { normalizeDecisionState, type DecisionState } from '../lib/types'
import { bucketColor } from '../lib/decisionColor'
import { tierLabel, TONE_COLOR } from '../lib/tierLabel'
import { DecisionStateBadge } from './Badges'

interface Props {
  calibratedConfidence: number
  rawConfidence: number
  decisionState: DecisionState
}

const RADIUS = 60
const STROKE = 12
const CIRCUMFERENCE = Math.PI * RADIUS // 半圓弧長

export default function ConfidenceGauge({ calibratedConfidence, rawConfidence, decisionState: rawDecisionState }: Props) {
  // #101：主角數字統一——abstain/low_confidence 態主角＝校準後資訊完整度，
  // normal 態主角＝裸均值信任分（`rawConfidence`，等同後端 `trust_score`），
  // 跟 OverviewCard/首頁卡/比較頁同一套規則，避免同一幣在不同頁面主角不一致。
  // #1 修復：legacy 快照／未知 enum 值一律先正規化為 'normal'（見
  // `normalizeDecisionState` docstring），跟 SSR 同一套 fallback 規則。
  const decisionState = normalizeDecisionState(rawDecisionState)
  const isLowInfo = decisionState === 'abstain' || decisionState === 'low_confidence'
  const heroValue = isLowInfo ? calibratedConfidence : rawConfidence
  const heroLabel = isLowInfo ? '資訊完整度（校準後）' : '信任分'
  const pct = Math.max(0, Math.min(1, heroValue))
  const color = bucketColor(decisionState, heroValue)
  // #171：離散分層標籤——一律吃後端對齊的 `calibrated_confidence`（資訊完整度），
  // 不是 gauge 大字 hero（normal 態 hero=rawConfidence 信任分）。因此本分層**正名為
  // 「資訊完整度」分層**，與下方「資訊完整度（校準後）」數字同源、可對照驗證，
  // 不冠「信任等級」以免被誤讀成上方信任分的分級（codex 對抗審 H-1：normal 態
  // 大字 95%＋等級「中」同框、raw/calibrated 兩來源互打的穿幫風險）。
  const tier = tierLabel(decisionState, calibratedConfidence)
  // normal 態明示分層主體＝資訊完整度；abstain/low_confidence 的 label 本身即
  // 自我描述的狀態語（棄權／資料不足、資訊完整度偏低），不再加前綴。
  const tierText = isLowInfo ? tier.label : `資訊完整度：${tier.label}`
  const dash = CIRCUMFERENCE * pct

  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-tf-border bg-tf-card p-4">
      <svg
        viewBox="0 0 140 80"
        width={180}
        height={104}
        role="img"
        aria-label={`${heroLabel} ${(pct * 100).toFixed(0)}%`}
      >
        <path
          d="M 10 70 A 60 60 0 0 1 130 70"
          fill="none"
          stroke="var(--color-tf-border)"
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
        <path
          d="M 10 70 A 60 60 0 0 1 130 70"
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${CIRCUMFERENCE}`}
        />
        <text
          x="70"
          y="62"
          textAnchor="middle"
          className="tf-num"
          fontSize="26"
          fontWeight="700"
          fill="var(--color-tf-text)"
        >
          {(pct * 100).toFixed(0)}%
        </text>
      </svg>
      <p
        className="tf-num text-lg font-bold leading-none"
        style={{ color: TONE_COLOR[tier.tone] }}
        aria-label={tierText}
      >
        {tierText}
      </p>
      <DecisionStateBadge state={decisionState} />
      <p className="tf-num text-xs text-tf-muted">
        資訊完整度（校準後） {calibratedConfidence.toFixed(2)}｜裸均值信任分 {rawConfidence.toFixed(2)}
      </p>
    </div>
  )
}
