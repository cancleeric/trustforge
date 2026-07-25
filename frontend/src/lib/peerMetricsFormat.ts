// 純顯示層格式化 helper（比照 `format.ts` 慣例）——`PeerMetricValue.value`
// 可能是 `null`（該指標缺席），一律顯示「—」，不偽裝成 0。

import type { PeerMetricValue } from './types'

/** 大數字（TVL 一類）縮寫成 K/M/B，避免表格擠爆；小數字原樣顯示。 */
function formatNumber(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`
  if (abs > 0 && abs < 0.01) return value.toFixed(6)
  return value.toFixed(2)
}

/** `metric` 整格缺席（`undefined`/`null`）或 `value` 為 `null` 都顯示
 * 「—」——誠實呈現「不知道」，不是「0」。 */
export function formatMetricValue(metric: PeerMetricValue | null | undefined): string {
  if (!metric || metric.value === null) return '—'
  const formatted = formatNumber(metric.value)
  return metric.unit ? `${formatted} ${metric.unit}` : formatted
}
