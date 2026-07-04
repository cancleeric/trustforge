import type { DecisionState } from '../lib/types'
import { DecisionStateBadge } from './Badges'

interface Props {
  calibratedConfidence: number
  rawConfidence: number
  decisionState: DecisionState
}

/** 對應後端 `Report.confidence_label()`：三態優先於純數字分桶。 */
function bucketColor(decisionState: DecisionState, calibrated: number): string {
  if (decisionState === 'abstain') return '#f85149'
  if (decisionState === 'low_confidence') return '#d9832a'
  if (calibrated >= 0.7) return '#3fb950'
  if (calibrated >= 0.45) return '#d9832a'
  return '#f85149'
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
          stroke="#30363d"
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
