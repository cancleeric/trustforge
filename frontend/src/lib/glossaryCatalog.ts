export type GlossaryAudience = 'report' | 'popover' | 'help_center'

export type GlossaryTermId =
  | 'fdv'
  | 'market_cap'
  | 'tvl'
  | 'tokenomics'
  | 'gas_fee'
  | 'unlock_sell_pressure'
  | 'trustScore'
  | 'completeness'
  | 'reputation'
  | 'corroboration'
  | 'recency'
  | 'manipulation'
  | 'divergence'
  | 'proxy'
  | 'rag'
  | 'washTrading'
  | 'spoofing'
  | 'multiSource'
  | 'freshness'
  | 'connector'
  | 'llmRuntime'
  | 'tokenUsage'
  | 'confidenceInterval'
  | 'dataSufficiency'
  | 'immutable'
  | 'lineage'
  | 'weight'

/**
 * #847：新手模式的短版白話文，滑過去就看得到。
 *
 * 刻意跟 `description` 分開存，不是覆寫它——這兩份文字給的是不同的人：
 *   - `description`：正式定義，出現在點開的名詞解釋卡與說明中心，**比賽方看的
 *     報告用的就是這一份**，措辭不能因為新手模式而改變。
 *   - `tooltip`：一句白話，只在新手模式滑過詞時浮出來，給一般使用者降低門檻。
 * 兩者同時存在，誰也不取代誰；關掉新手模式時報告與現在一字不差。
 *
 * 一定要雙語。詞庫其他欄位是單一字串（有些條目本來就是英文），沒有語系維度，
 * 如果 tooltip 也寫死中文，`locale=en` 的使用者會滑出一排中文——這正是既有
 * 英文版殘留中文那類問題再擴大一次。所以這個欄位從第一天就帶語系。
 */
export type GlossaryTooltip = { 'zh-TW': string; en: string }

export type GlossaryCatalogTerm = {
  term_id: GlossaryTermId
  label: string
  description: string
  aliases: string[]
  audiences: GlossaryAudience[]
  where?: string
  riskNote?: string
  /** #847：新手模式短版白話文（≤15 字）。見 GlossaryTooltip 的說明。 */
  tooltip?: GlossaryTooltip
}

const canonicalKey = (value: string) => value.trim().toLocaleLowerCase().replace(/\s+/g, ' ')

export function validateGlossaryCatalog(terms: GlossaryCatalogTerm[]) {
  const seenIds = new Set<string>()
  const seenPhrases = new Map<string, GlossaryTermId>()
  for (const term of terms) {
    const termId = canonicalKey(term.term_id)
    if (seenIds.has(termId)) throw new Error(`duplicate glossary term_id: ${term.term_id}`)
    seenIds.add(termId)

    for (const phrase of [term.label, ...term.aliases]) {
      const key = canonicalKey(phrase)
      const owner = seenPhrases.get(key)
      if (owner && canonicalKey(owner) !== termId) {
        throw new Error(`duplicate glossary alias: ${key}`)
      }
      seenPhrases.set(key, term.term_id)
    }
  }
  return terms
}

export const GLOSSARY_CATALOG: GlossaryCatalogTerm[] = validateGlossaryCatalog([
  {
    term_id: 'fdv',
    label: 'FDV',
    description: 'Fully diluted valuation；假設所有代幣都進入流通後估算出的完全稀釋估值。',
    aliases: ['fully diluted valuation'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 名詞標註',
    riskNote:
      'FDV can be far higher than circulating market cap when most supply is still locked; comparing FDV across assets with very different unlock schedules can be misleading.',
  },
  {
    term_id: 'market_cap',
    label: 'MC',
    description: 'Market capitalization；流通供給乘上目前市場價格得到的市值。',
    aliases: ['market cap', 'market capitalization', '市值'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 同層比較',
  },
  {
    term_id: 'tvl',
    label: 'TVL',
    description: 'Total value locked；在特定觀測時間存放於協議或鏈上的資產總價值。',
    aliases: ['total value locked'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 同層比較',
    riskNote:
      'TVL reflects deposited assets, not usage or revenue, and can be inflated by incentive programs or double-counted across composable protocols.',
  },
  {
    term_id: 'tokenomics',
    label: 'Tokenomics',
    description: '代幣供給、分配、釋放、用途與激勵設計的整體結構。',
    aliases: ['token economics', '代幣經濟'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 風險提示',
    riskNote:
      'Vesting cliffs and team/investor allocation shares vary widely between assets, which can make tokenomics comparisons across projects misleading.',
  },
  {
    term_id: 'gas_fee',
    label: 'Gas Fee',
    description: '執行或結算交易時支付給網路的費用。',
    aliases: ['gas', 'transaction fee', '手續費'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 成本',
    riskNote:
      'A token usually cannot be used to pay its own transfer fees; fees are paid in the gas token accepted by the network where the transaction executes. For example, transactions on Arbitrum (an Ethereum L2) are typically paid in ETH, not in ARB.',
  },
  {
    term_id: 'unlock_sell_pressure',
    label: '解鎖賣壓',
    description: '先前鎖定的代幣變成可轉讓後，可能形成的潛在賣出壓力。',
    aliases: ['unlock pressure', 'token unlock', 'unlock sell pressure'],
    audiences: ['report', 'popover', 'help_center'],
    where: '分析結果 · 風險提示',
    riskNote:
      '解鎖後流通供給增加，可能形成賣壓；實際影響需視解鎖排程、解鎖量佔流通量比例與市場深度而定，非必然下跌。',
  },
  {
    term_id: 'trustScore',
    label: '信任分數',
    description: '綜合來源信譽、交叉佐證、資料時效與抗操縱能力的可信程度；不是價格漲跌機率。',
    tooltip: { 'zh-TW': '這條消息多可信，不是預測漲跌', en: 'How trustworthy this claim is — not a price call' },
    aliases: ['Trust Score'],
    audiences: ['popover', 'help_center'],
    where: '分析結果 · 執行台 · 歷史趨勢',
  },
  {
    term_id: 'completeness',
    label: '資訊完整度',
    description: '本次可用資料是否足以支持判讀。完整度低代表證據不足，不代表風險較低。',
    tooltip: { 'zh-TW': '這次分析的資料夠不夠', en: 'Whether we had enough data this time' },
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'reputation',
    label: '來源信譽',
    description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表來源過去可靠度與交叉核對後表現。',
    tooltip: { 'zh-TW': '這個來源過去靠不靠譜', en: 'How reliable this source has been' },
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'corroboration',
    label: '交叉佐證',
    description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表有多少彼此獨立來源支持同一說法。',
    tooltip: { 'zh-TW': '有幾個不相關的來源也這樣說', en: 'How many independent sources agree' },
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'recency',
    label: '資料時效',
    description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表資料距離現在有多久。',
    tooltip: { 'zh-TW': '資料多新，越舊分越低', en: 'How recent the data is — older scores lower' },
    aliases: [],
    audiences: ['popover', 'help_center'],
    where: '分析結果 · freshness',
  },
  {
    term_id: 'manipulation',
    label: '抗操縱能力',
    description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表資料抵抗喊單、誇大承諾與協同行為的程度。',
    tooltip: { 'zh-TW': '有沒有被灌水或帶風向', en: 'Whether the data looks pumped or astroturfed' },
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'divergence',
    label: '跨來源分歧',
    description: '不同來源對同一問題得出互相衝突的訊號；分歧越大，結論越需要保守解讀。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'proxy',
    label: '替代指標',
    description: '直接資料不足時使用的間接觀測值，必須標示限制與來源。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'rag',
    label: 'RAG',
    description: 'Retrieval-Augmented Generation；先取回證據，再讓模型根據證據生成摘要或判讀。',
    aliases: [],
    audiences: ['popover', 'help_center'],
    where: '運作原理',
  },
  {
    term_id: 'washTrading',
    label: '洗量交易',
    description: '同一方或關聯方反覆買賣製造虛假成交量。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'spoofing',
    label: '掛單誘導',
    description: '放出不打算成交的大額掛單，誘導市場錯估供需。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'multiSource',
    label: 'multi_source 模式',
    description: '同時比對多個交易所與資料源，交叉驗證後才給分，比單一來源更難被造假。',
    tooltip: { 'zh-TW': '同時看多個來源，不只看一家', en: 'Checks many sources, not just one' },
    aliases: ['Multi-source'],
    audiences: ['popover', 'help_center'],
    where: '分析結果 · 執行台 · 成本',
  },
  {
    term_id: 'freshness',
    label: 'Freshness',
    description: '資料新鮮度。TrustForge 會標出抓取時間與 stale 狀態，避免把舊資料當成即時訊號。',
    tooltip: { 'zh-TW': '資料抓回來多久了', en: 'How long ago the data was fetched' },
    aliases: ['新鮮度'],
    audiences: ['popover', 'help_center'],
    where: '分析結果 · 來源',
  },
  {
    term_id: 'connector',
    label: 'Connector',
    description: '連接外部資料源的模組，負責抓取、驗證、快取與標記來源。',
    aliases: [],
    audiences: ['popover', 'help_center'],
    where: '運作原理',
  },
  {
    term_id: 'llmRuntime',
    label: 'LLM Runtime',
    description: '執行模型推理的後端環境與供應商。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'tokenUsage',
    label: 'Token Usage',
    description: '模型輸入與輸出的 token 用量，通常會影響成本。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'confidenceInterval',
    label: '信賴區間',
    description: '模型或統計估計的不確定範圍，範圍越寬代表結果越不穩定。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'dataSufficiency',
    label: '資料充足性',
    description: '判斷資料量與品質是否足以支撐結論。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'immutable',
    label: '不可變證據',
    description: '寫入後不應被覆蓋的證據紀錄，方便後續稽核與重播。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'lineage',
    label: '資料 lineage',
    description: '資料從來源、轉換到輸出的完整追蹤脈絡。',
    aliases: [],
    audiences: ['popover'],
  },
  {
    term_id: 'weight',
    label: '權重',
    description: '不同訊號在綜合分數中的相對影響程度。',
    tooltip: { 'zh-TW': '各評分項目的重要程度比例', en: 'How much each factor counts toward the score' },
    aliases: [],
    audiences: ['popover'],
  },
])

export const GLOSSARY_BY_ID = Object.fromEntries(
  GLOSSARY_CATALOG.map((term) => [term.term_id, term]),
) as Record<GlossaryTermId, GlossaryCatalogTerm>

export const HELP_CENTER_GLOSSARY = GLOSSARY_CATALOG.filter((term) =>
  term.audiences.includes('help_center'),
)
