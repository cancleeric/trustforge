import type { CrossSourceSignal, StancePair } from './types'

/**
 * 依 stance 陣營（bullish/bearish）分組並依 `source` 去重（#13 修正）。
 *
 * 背景：`stance_pairs` 是逐筆明細，去重鍵是 `claim_id`——同一來源若有兩則
 * 不同 claim 各自跟不同對手配對成功，會在 `stance_pairs` 裡出現兩次。直接
 * 拿 `stance_pairs` 分組渲染，會讓畫面上同一來源出現兩張卡片，視覺上等同
 * 「兩個獨立來源支持」，但實際只有一個來源——這是誤導使用者的計數 bug。
 *
 * 優先使用後端已算好的 `signal.distinct_sources`（正確的資料源頭，見
 * `orchestrator._dedup_stance_pairs_by_source`：同一來源同一陣營只留一筆
 * 代表）。若後端回應沒有這個欄位（例如尚未升級的快取/舊資料），退回在
 * 前端自行依 `source` 去重，作為防禦性 fallback——後端才是真正的資料
 * 源頭，前端去重只是治標，兩者一起修才是正確做法。
 */
export function groupByStance(
  signal: Pick<CrossSourceSignal, 'stance_pairs' | 'distinct_sources'> | null | undefined,
): { bullish: StancePair[]; bearish: StancePair[] } {
  if (signal?.distinct_sources) {
    return {
      bullish: signal.distinct_sources.bullish ?? [],
      bearish: signal.distinct_sources.bearish ?? [],
    }
  }

  const pairs = signal?.stance_pairs ?? []
  const bullish: StancePair[] = []
  const bearish: StancePair[] = []
  const seenBullish = new Set<string>()
  const seenBearish = new Set<string>()
  for (const p of pairs) {
    if (p.stance === 'bullish' || p.stance === '看漲' || p.stance === 'BULLISH') {
      if (seenBullish.has(p.source)) continue
      seenBullish.add(p.source)
      bullish.push(p)
    } else if (p.stance === 'bearish' || p.stance === '看跌' || p.stance === 'BEARISH') {
      if (seenBearish.has(p.source)) continue
      seenBearish.add(p.source)
      bearish.push(p)
    }
  }
  return { bullish, bearish }
}
