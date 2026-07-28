// 對應後端 `src/trustforge/brand_logos.py` 的顯示名/tier 邏輯（純資料
// 對照，不含任何 SVG/LOGO——本專案禁 `dangerouslySetInnerHTML`，一律用
// 純 React 元件畫 tier 徽章，不消費後端 inline SVG 字串）。

const SOURCE_DISPLAY_NAME_FINE: Record<string, string> = {
  'coingecko-price': 'CoinGecko · 即時報價',
  'coingecko-sentiment': 'CoinGecko · 社群情緒',
  'coingecko-dev': 'CoinGecko · 開發活動',
  'reddit-cryptocurrency': 'Reddit · r/CryptoCurrency',
  'reddit-bitcoin': 'Reddit · r/Bitcoin',
  coindesk: 'CoinDesk',
  decrypt: 'Decrypt',
  cryptopanic: 'CryptoPanic',
  'alternative-me-fng': 'Alternative.me · 恐懼貪婪指數',
  'blockchain-info': 'Blockchain.com',
  'sec-gov': '美國 SEC',
  'ohlcv-csv': 'HOYA BIT · 官方 OHLCV',
  cointelegraph: 'CoinTelegraph',
  bitcoinmagazine: 'Bitcoin Magazine',
  cryptoslate: 'CryptoSlate',
  bitcoinist: 'Bitcoinist',
  newsbtc: 'NewsBTC',
  dailyhodl: 'The Daily Hodl',
  theblock: 'The Block',
  utoday: 'U.Today',
  blockworks: 'Blockworks',
  'mempool-space-fees': 'mempool.space · 建議手續費',
  'mempool-space-difficulty': 'mempool.space · 難度調整進度',
  blockchair: 'Blockchair · BTC 鏈上統計',
  'whale-alert': 'Whale Alert · 鯨魚大額轉帳',
  'arkham-intel': 'Arkham · 標記錢包交易',
}

/** 任何使用者可見處都應呼叫這支，不得直接印 `evidence.source` 原始 slug。 */
export function sourceDisplayName(source: string): string {
  if (source in SOURCE_DISPLAY_NAME_FINE) return SOURCE_DISPLAY_NAME_FINE[source]
  if (!source) return '未知來源'
  return source
    .replace(/-/g, ' ')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

const OFFICIAL_KINDS = new Set(['price', 'hoyabit', 'regulatory'])
const THIRD_PARTY_KINDS = new Set(['price_live', 'onchain', 'whale_onchain', 'celebrity_trade'])
const COMMUNITY_KINDS = new Set(['news', 'social', 'sentiment'])

export interface IndependenceTier {
  label: string
  color: string
}

/** 對應後端 `_independence_tier()`：層級·權威性徽章。 */
export function independenceTier(kind: string): IndependenceTier {
  if (OFFICIAL_KINDS.has(kind)) return { label: '高·官方', color: 'var(--color-tf-good)' }
  if (THIRD_PARTY_KINDS.has(kind)) return { label: '高·第三方', color: 'var(--color-tf-good)' }
  if (COMMUNITY_KINDS.has(kind)) return { label: '中·社群', color: 'var(--color-tf-warn)' }
  return { label: '一般·輔助', color: 'var(--color-tf-muted)' }
}
