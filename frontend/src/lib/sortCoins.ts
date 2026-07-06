import type { OverviewCoin } from './types'

/**
 * #86：跨幣信任排行——依 `trust_score` 降序排列。抽成獨立純函式（不推導
 * 後端未提供的欄位，純粹排序既有資料），方便 vitest 純單元測試排序/
 * 平手行為，不需要渲染 `HomePage` 元件。
 *
 * 平手行為（`trust_score` 相同）：`Array.prototype.sort` 在 ES2019+
 * 引擎上保證 stable sort，相同排序鍵的元素維持**呼叫端傳入陣列的原始
 * 相對順序**（= `/api/overview` 回傳順序，即後端 `COIN_POOL` 既有順序）
 * ——這裡刻意不加次要排序鍵（如幣別字母序），讓「平手時維持原順序」成為
 * 明確、可測試、確定性的行為，不是「沒定義／看引擎心情」。
 *
 * 回傳新陣列（`[...coins]` 後才 `sort`），不就地改動呼叫端傳入的原始
 * 陣列／API response 物件。
 */
export function sortCoinsByTrustScoreDesc(coins: OverviewCoin[]): OverviewCoin[] {
  return [...coins].sort((a, b) => b.trust_score - a.trust_score)
}
