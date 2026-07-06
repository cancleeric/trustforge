import type { DecisionState } from '../lib/types'
import { independenceTier } from '../lib/sourceBrand'
import { manipRiskDisplay } from '../lib/manipRisk'

function pillClasses(color: string) {
  return {
    color,
    borderColor: color,
    backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`,
  }
}

export function DirectionBadge({ direction }: { direction: string }) {
  const color =
    direction === '偏多'
      ? 'var(--color-tf-good)'
      : direction === '偏空'
        ? 'var(--color-tf-bad)'
        : 'var(--color-tf-muted)'
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
  const color =
    state === 'normal'
      ? 'var(--color-tf-good)'
      : state === 'low_confidence'
        ? 'var(--color-tf-warn)'
        : 'var(--color-tf-bad)'
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
      className="inline-flex items-center gap-1 rounded-full border border-tf-bad bg-[color-mix(in_srgb,var(--color-tf-bad)_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-bad"
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
      className="inline-flex items-center gap-1 rounded-full border border-tf-bad bg-[color-mix(in_srgb,var(--color-tf-bad)_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-bad"
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
      className="inline-flex items-center gap-1 rounded-full border border-tf-accent bg-[color-mix(in_srgb,var(--color-tf-accent)_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-link"
      title={infoFlags.join('、')}
    >
      &#8505; 相似簇
    </span>
  )
}

export function SingleSourceBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_14%,transparent)] px-1.5 py-0.5 text-[0.65rem] font-semibold text-tf-warn"
      title="此維度目前僅有單一來源支撐，尚未有跨源互證"
    >
      單源
    </span>
  )
}

const FRESHNESS_STATUS_LABEL: Record<'fresh' | 'stale' | 'missing', string> = {
  fresh: '新鮮',
  stale: '過期',
  missing: '缺席',
}

/** `/api/status` 鮮度矩陣單格狀態——語意同 `DecisionStateBadge`
 * （狀態徽章一律用膠囊 `rounded-full`，跟 `TierBadge` 這類分類標籤
 * `rounded` 區分，見 `docs/archive/plans/UXUI-ROUND-01.md` #2 圓角 token 統一建議）。 */
export function FreshnessStatusBadge({ status }: { status: 'fresh' | 'stale' | 'missing' }) {
  const color =
    status === 'fresh'
      ? 'var(--color-tf-good)'
      : status === 'stale'
        ? 'var(--color-tf-warn)'
        : 'var(--color-tf-muted)'
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold"
      style={pillClasses(color)}
    >
      {FRESHNESS_STATUS_LABEL[status]}
    </span>
  )
}

/** #86：首頁跨幣信任排行的操縱風險徽章——依 `OverviewCoin.manip_score`
 * （`_calc_manip_signal()` 的 worst-case max，見 `lib/manipRisk.ts`
 * docstring）分級：≥0.3 高風險／≥0.1 中風險／其餘低風險。閾值沿用
 * issue #86 定案數字，跟 `web.py::_render_trust_breakdown` 的操縱分項紅色
 * 判斷（manip>0 即標紅）不同量級——這裡是「多幣排行」的相對分級展示，
 * 非單幣證據明細的逐筆判斷。分級邏輯本身抽到 `manipRiskDisplay()`（純函式，
 * 不依賴 React），這個元件只負責渲染，方便 vitest 純單元測試分級/門檻/
 * 缺分數行為，不需要另裝 RTL/jsdom。
 *
 * codex 複審 MEDIUM 修復：`manip_score` 為 `undefined`（本輪無
 * evidence，或舊格式快照本欄位新增前寫入）時**顯式**顯示「操縱風險未
 * 評分」中性態徽章（中性灰，非低風險的綠色）——不能悄悄不顯示整顆
 * 徽章，否則「沒評分」跟「評分後風險低」在卡片上無法區分，使用者會把
 * 兩者都誤讀成「安全」（#24 誠實原則，同 `SingleSourceBadge`／
 * `reputation_trace` 選填欄位的 has_data 處理慣例，但這裡刻意選擇「顯示
 * 中性態」而非「完全不渲染」，避免二度混淆）。
 *
 * codex 複審 delta HIGH 修復：分級判斷（含 legacy payload 偵測）全部下放給
 * `manipRiskDisplay(manipScore, manipScoreMean)`（見該函式 docstring），
 * 這裡只依 `legacy` 旗標決定 tooltip 文案——legacy payload（有
 * `manipScore` 但沒有 `manipScoreMean`）代表這包快照是舊 writer 寫的，
 * `manipScore` 語意不可信（可能仍是舊的平均值），不能直接顯示成
 * worst-case 數字，故 tooltip 改講「資料格式待刷新」而非硬印一個不可信
 * 的分數。 */
export function ManipRiskBadge({
  manipScore,
  manipScoreMean,
}: {
  manipScore: number | undefined
  manipScoreMean?: number
}) {
  const { label, color, legacy } = manipRiskDisplay(manipScore, manipScoreMean)
  const title =
    manipScore === undefined
      ? '本輪快照缺 evidence，尚未計算操縱風險（非「已評估、無風險」）'
      : legacy
        ? '本輪快照為舊版格式（僅含平均值、無 worst-case 輔助欄位），操縱風險暫不顯示，待下次刷新'
        : // legacy 為 false 且 manipScoreMean 存在，此分支下 manipScoreMean 必定有值
          `操縱風險（worst-case）${manipScore.toFixed(2)}；平均 ${(manipScoreMean as number).toFixed(2)}（僅供輔助判讀）`
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold"
      style={pillClasses(color)}
      title={title}
    >
      {label}
    </span>
  )
}

/** `/api/status` cache backend 降級旗標——`degraded:true` 代表 primary
 * outage、正在靠本地 `JsonCacheBackend` fallback 撐著（見
 * `_handle_api_status` docstring），必須顯眼標示，不能悄悄回 200 就當沒事。 */
export function DegradedBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_14%,transparent)] px-2 py-0.5 text-xs font-semibold text-tf-warn"
      title="Primary cache backend 目前不可用，正使用本地 fallback"
    >
      &#9888; 降級中（fallback）
    </span>
  )
}
