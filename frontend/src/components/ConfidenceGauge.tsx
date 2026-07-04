import type { DecisionState } from '../lib/types'
import { DecisionStateBadge } from './Badges'

interface Props {
  calibratedConfidence: number
  rawConfidence: number
  decisionState: DecisionState
}

/** 對應後端 `Report.confidence_label()`：三態優先於純數字分桶。
 *  回傳 CSS var 引用（而非寫死 hex），確保 light/dark 切主題時 gauge
 *  顏色跟著 `--color-tf-*` token 一起變，不會卡在 dark 色。 */
function bucketColor(decisionState: DecisionState, calibrated: number): string {
  if (decisionState === 'abstain') return 'var(--color-tf-bad)'
  if (decisionState === 'low_confidence') return 'var(--color-tf-warn)'
  if (calibrated >= 0.7) return 'var(--color-tf-good)'
  if (calibrated >= 0.45) return 'var(--color-tf-warn)'
  return 'var(--color-tf-bad)'
}

const RADIUS = 60
const STROKE = 12
const CIRCUMFERENCE = Math.PI * RADIUS // 半圓弧長

export default function ConfidenceGauge({ calibratedConfidence, rawConfidence, decisionState }: Props) {
  const pct = Math.max(0, Math.min(1, calibratedConfidence))
  const color = bucketColor(decisionState, calibratedConfidence)
  const dash = CIRCUMFERENCE * pct

  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-tf-border bg-tf-card p-4">
      <svg
        viewBox="0 0 140 80"
        width={180}
        height={104}
        role="img"
        aria-label={`校準後信心 ${(pct * 100).toFixed(0)}%`}
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
      <DecisionStateBadge state={decisionState} />
      <p className="tf-num text-xs text-tf-muted">校準後信心 {pct.toFixed(2)}｜裸均值 {rawConfidence.toFixed(2)}</p>
    </div>
  )
}
