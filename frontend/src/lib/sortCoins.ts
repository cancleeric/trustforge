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

/**
 * codex 窮舉終審 LOW 修復（平手 rank）：`HomePage` 原本直接拿排序後的
 * array index 當名次（`i + 1`）——`trust_score` 相同的兩幣會因為只是
 * 陣列位置不同而顯示不同名次（例如並列第 2 名的兩幣被標成「第 2 名」跟
 * 「第 3 名」），跟「平手」這件事本身矛盾。改用**competition ranking**
 * （1224 制）：分數相同的並列同一個名次，下一個較低分數的名次直接跳到
 * 「目前已出現的項目數 + 1」（不是「不同名次數 + 1」），例如四幣分數
 * `[10, 8, 8, 5]` 排名是 `[1, 2, 2, 4]`（不是奧運排名制的 `[1,2,2,3]`）。
 *
 * ⚠️ 前提：`coins` 必須已經是 `sortCoinsByTrustScoreDesc()` 排序後的降序
 * 陣列——這個函式不會自己排序，只依「陣列既有順序」逐一比對相鄰
 * `trust_score` 是否相同，順序不對名次就會算錯。
 *
 * 回傳一個跟輸入陣列等長、逐一對應的名次陣列，不改動 `coins` 本身。
 */
export function computeCompetitionRanks(coins: OverviewCoin[]): number[] {
  const ranks: number[] = []
  for (let i = 0; i < coins.length; i++) {
    if (i > 0 && coins[i].trust_score === coins[i - 1].trust_score) {
      ranks.push(ranks[i - 1])
    } else {
      ranks.push(i + 1)
    }
  }
  return ranks
}
