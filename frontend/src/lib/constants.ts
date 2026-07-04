// 對應後端 `src/trustforge/schema.py::COIN_POOL`（官方幣種池）。前端只用來
// 做 UI 白名單防呆（下拉選單、卡片連結），實際驗證仍以後端為準。
export const COIN_POOL = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'] as const

export type CoinSymbol = (typeof COIN_POOL)[number]

export const QUESTION_TYPES = [
  { value: 'multi_source', label: '多源整合' },
  { value: 'hypothesis', label: '假設驗證' },
] as const
