import { COIN_POOL, type CoinSymbol } from './constants'

export type CompetitionQuestion = Readonly<{
  id: string
  coin: CoinSymbol
  query: string
}>

export type RandomSource = () => number

/** Fixed competition prompt forms. The selected asset is applied at pick time,
 * so filling the textarea can never silently change or disagree with the coin
 * control. */
export const COMPETITION_QUESTION_TEMPLATES = [
  '整合近兩週價格、成交量、鏈上、新聞與社群訊號，整理市場判斷與限制。',
  '說明價格變動是否有鏈上流量或交易所資金流佐證；若沒有，清楚標示證據缺口。',
  '比對新聞與社群情緒是否一致，並指出來源時效與可能的操弄風險。',
  '檢查政府公告或監管文件是否改變近期風險背景，列出可回溯來源。',
  '以多來源資料判斷目前是趨勢延續、反轉，或資訊不足；不要給交易指令。',
] as const

export const COMPETITION_COINS = COIN_POOL

export function isCompetitionCoin(value: string): value is CoinSymbol {
  return (COMPETITION_COINS as readonly string[]).includes(value)
}

export function pickCompetitionQuestion(
  coin: CoinSymbol,
  random: RandomSource = Math.random,
  templates: readonly string[] = COMPETITION_QUESTION_TEMPLATES,
): CompetitionQuestion {
  if (!COMPETITION_COINS.includes(coin)) throw new RangeError('Coin is outside the competition scope')
  if (templates.length === 0) throw new Error('Competition question bank must not be empty')
  const sample = random()
  if (!Number.isFinite(sample) || sample < 0 || sample >= 1) {
    throw new RangeError('Random source must return a finite value in [0, 1)')
  }
  const index = Math.floor(sample * templates.length)
  return {
    id: `competition-${coin.toLowerCase()}-${index + 1}`,
    coin,
    query: `請分析 ${coin}：${templates[index]}`,
  }
}
