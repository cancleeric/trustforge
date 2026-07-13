import type { CrossSourceSignal, Evidence } from '../lib/types'
import { groupByStance } from '../lib/stancePairs'

/** 獨立來源數：直接對 `evidence[].source` 取 raw set 去重——刻意與後端
 * `schema.py::comparison_to_markdown` 的 `len({e.source for e in evidence})`
 * 同一套語意（不另做大小寫/空白正規化），保證前後端計數一致、不各自
 * 發明一套 normalization；evidence source 皆為定型 slug（如 `coindesk`），
 * 不存在 `groupByStance` 那種「同 publisher 大小寫/空白變體」的去重需求
 * （那是 stance_pairs 逐筆明細的問題，見 `lib/stancePairs.ts`）。 */
function distinctSourceCount(evidence: Evidence[]): number {
  return new Set(evidence.map((e) => e.source)).size
}

function manipulationFlagCount(evidence: Evidence[]): number {
  return evidence.filter((e) => e.flags.length > 0).length
}

function infoFlagCount(evidence: Evidence[]): number {
  return evidence.filter((e) => e.info_flags.length > 0).length
}

function StatCard({
  label,
  value,
  color,
  sub,
}: {
  label: string
  value: string
  color: string
  sub?: string
}) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-3">
      <p className="text-[0.7rem] font-medium text-tf-muted">{label}</p>
      <p className="tf-num mt-1 text-2xl font-bold leading-none" style={{ color }}>
        {value}
      </p>
      {sub ? <p className="mt-1 text-[0.68rem] leading-snug text-tf-muted">{sub}</p> : null}
    </div>
  )
}

/** 信任溯源概覽卡片（#171 第 2 項）：一組 compact stat cards，**只**聚合
 * 真實既有欄位，絕不造數。任一資料缺席（如 `cross_source_signal` 為
 * null）一律顯「無法判定」中性態，不補 0、不做任何分數/信賴區間推導。
 *
 * 配色沿用專案既有 token（見 `TrustBreakdown` / `CrossSourceSignalPanel`）：
 * 操縱紅旗用 `tf-bad`（紅），中性相似提示用 `tf-accent`（藍，絕不用操縱
 * 紅旗樣式），跨源共識/背離分別用 `tf-good` / `tf-warn`，無法判定用
 * `tf-muted`。 */
export default function EvidenceTrailPanel({
  evidence,
  signal,
}: {
  evidence: Evidence[]
  signal: CrossSourceSignal | null
}) {
  const sources = distinctSourceCount(evidence)
  const manipFlags = manipulationFlagCount(evidence)
  const infoFlags = infoFlagCount(evidence)

  let signalColor = 'var(--color-tf-muted)'
  let signalValue = '無法判定'
  let signalSub: string | undefined

  if (signal !== null) {
    if (signal.type === 'consensus') {
      signalColor = 'var(--color-tf-good)'
      signalValue = '多源共識'
    } else {
      signalColor = 'var(--color-tf-warn)'
      signalValue = '訊號背離'
      const grouped = groupByStance(signal)
      signalSub = `偏多 ${grouped.bullish.length} 來源 ／ 偏空 ${grouped.bearish.length} 來源（去重）`
    }
  }

  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">信任溯源概覽</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="證據總筆數" value={String(evidence.length)} color="var(--color-tf-text)" />
        <StatCard label="獨立來源數" value={String(sources)} color="var(--color-tf-text)" />
        <StatCard
          label="操縱紅旗筆數"
          value={String(manipFlags)}
          color="var(--color-tf-bad)"
          sub={manipFlags > 0 ? '已確認操縱，反映在信任分' : '無紅旗'}
        />
        <StatCard
          label="中性相似提示筆數"
          value={String(infoFlags)}
          color="var(--color-tf-accent)"
          sub={infoFlags > 0 ? '類似度高，待人工判讀' : '無相似簇'}
        />
        <StatCard label="跨源訊號" value={signalValue} color={signalColor} sub={signalSub} />
      </div>
    </div>
  )
}
