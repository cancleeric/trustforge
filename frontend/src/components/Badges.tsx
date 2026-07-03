import type { DecisionState } from '../lib/types'
import { independenceTier } from '../lib/sourceBrand'

function pillClasses(color: string) {
  return {
    color,
    borderColor: color,
    backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`,
  }
}

export function DirectionBadge({ direction }: { direction: string }) {
  const color =
    direction === '偏多' ? '#3fb950' : direction === '偏空' ? '#f85149' : '#8b949e'
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold"
      style={pillClasses(color)}
    >
      {direction || '不明'}
    </span>
  )
}

const DECISION_STATE_LABEL: Record<DecisionState, string> = {
  abstain: '棄權／資料不足',
  low_confidence: '低信心',
  normal: '正常判斷',
}

export function DecisionStateBadge({ state }: { state: DecisionState }) {
  const color = state === 'normal' ? '#3fb950' : state === 'low_confidence' ? '#d9832a' : '#f85149'
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold"
      style={pillClasses(color)}
    >
      {DECISION_STATE_LABEL[state] ?? state}
    </span>
  )
}

export function TierBadge({ kind }: { kind: string }) {
  const tier = independenceTier(kind)
  return (
    <span
      className="inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wide"
      style={pillClasses(tier.color)}
      title={`資料類型：${kind}`}
    >
      {tier.label}
    </span>
  )
}

export function LowTrustBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-bad bg-[color-mix(in_srgb,#f85149_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-bad"
      title="信任分低於 0.3"
    >
      &#9888; 低信任/操縱
    </span>
  )
}

export function FlagBadge({ flags }: { flags: string[] }) {
  if (!flags.length) return null
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-bad bg-[color-mix(in_srgb,#f85149_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-bad"
      title={`操縱關鍵詞：${flags.join('、')}`}
    >
      &#128681; {flags.join('、')}
    </span>
  )
}

export function InfoFlagBadge({ infoFlags }: { infoFlags: string[] }) {
  if (!infoFlags.length) return null
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-accent bg-[color-mix(in_srgb,#1f6feb_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-link"
      title={infoFlags.join('、')}
    >
      &#8505; 相似簇
    </span>
  )
}

export function SingleSourceBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-warn bg-[color-mix(in_srgb,#d9832a_14%,transparent)] px-1.5 py-0.5 text-[0.65rem] font-semibold text-tf-warn"
      title="此維度目前僅有單一來源支撐，尚未有跨源互證"
    >
      單源
    </span>
  )
}
