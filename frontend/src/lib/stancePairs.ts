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
 * 代表，去重 key 已正規化 `source.strip().casefold()`）。若後端回應沒有
 * 這個欄位（例如尚未升級的快取/舊資料），退回在前端自行依 `source` 去重，
 * 作為防禦性 fallback——後端才是真正的資料源頭，前端去重只是治標，兩者
 * 一起修才是正確做法。
 *
 * fallback 去重同樣做輕量正規化（`trim().toLowerCase()`，對齊後端
 * `casefold()`），治掉大小寫/前後空白變體（如 `"CoinDesk"` / `" coindesk "`
 * / `"COINDESK"`）被誤判成不同來源；**顯示仍用原始 `source` 字串**（保留
 * 第一筆出現時的原始格式）。完整 canonical source identity（別名/帳號
 * 收斂）不在本輪範圍，見 repo-wide follow-up issue。
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
  const normalize = (source: string) => source.trim().toLowerCase()
  for (const p of pairs) {
    if (p.stance === 'bullish' || p.stance === '看漲' || p.stance === 'BULLISH') {
      const key = normalize(p.source)
      if (seenBullish.has(key)) continue
      seenBullish.add(key)
      bullish.push(p)
    } else if (p.stance === 'bearish' || p.stance === '看跌' || p.stance === 'BEARISH') {
      const key = normalize(p.source)
      if (seenBearish.has(key)) continue
      seenBearish.add(key)
      bearish.push(p)
    }
  }
  return { bullish, bearish }
}
