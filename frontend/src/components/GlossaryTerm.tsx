import { useEffect, useId, useRef, useState } from 'react'

export type GlossaryKey =
  | 'trustScore' | 'completeness' | 'reputation' | 'corroboration' | 'recency' | 'manipulation' | 'divergence' | 'proxy' | 'rag'
  | 'washTrading' | 'spoofing' | 'multiSource' | 'freshness' | 'connector' | 'llmRuntime'
  | 'tokenUsage' | 'confidenceInterval' | 'dataSufficiency' | 'immutable' | 'lineage' | 'weight'

const GLOSSARY: Record<GlossaryKey, { title: string; description: string }> = {
  trustScore: { title: '信任分數', description: '綜合來源信譽、交叉佐證、資料時效與抗操縱能力的可信程度；不是價格漲跌機率。' },
  completeness: { title: '資訊完整度', description: '本次可用資料是否足以支持判讀。完整度低代表證據不足，不代表風險較低。' },
  reputation: { title: '來源信譽', description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表來源過去可靠度與交叉核對後表現。' },
  corroboration: { title: '交叉佐證', description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表有多少彼此獨立來源支持同一說法。' },
  recency: { title: '資料時效', description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表資料距離現在有多久。' },
  manipulation: { title: '抗操縱能力', description: '獨立 0-100 子分數，不是加權係數，也不應與其他軸相加；代表資料抵抗喊單、誇大承諾與協同行為的程度。' },
  divergence: { title: '跨來源分歧', description: '不同來源對同一問題得出互相衝突的訊號；分歧越大，結論越需要保守解讀。' },
  proxy: { title: '總覽代理值', description: '正式分析尚未完成時，由市場總覽推導的暫時估計；不能取代證據綁定的正式結果。' },
  rag: { title: '相似歷史題目', description: '從過去分析中找相似問題作為參考。它不屬於本次證據，也不參與本次信任評分。' },
  washTrading: { title: 'wash-trading 疑慮', description: '自買自賣（對敲）製造假成交量的嫌疑，會讓成交量看起來比實際熱絡。' },
  spoofing: { title: 'spoofing 訊號', description: '掛出大量假委託、再快速撤單，誘導市場方向的操縱手法訊號。' },
  multiSource: { title: 'multi_source 模式', description: '同時比對多個交易所與資料源，交叉驗證後才給分，比單一來源更難被造假。' },
  freshness: { title: '新鮮度', description: '資料的即時程度。FRESH＝最新、STALE＝逾期待更新、MISSING＝來源缺席未回應。' },
  connector: { title: '連接器', description: '串接各交易所與資料源的介接元件，負責持續抓取報價、成交量與情緒資料。' },
  llmRuntime: { title: 'LLM Runtime · Bedrock', description: '驅動 HERMES 推理與生成報告的大型語言模型執行環境（AWS Bedrock）。' },
  tokenUsage: { title: 'TOKEN 用量', description: 'LLM 處理文字的計費單位。一次 run 消耗的 token 越多，成本越高。' },
  confidenceInterval: { title: '完整度區間', description: '資訊完整度的合理波動範圍。區間越窄代表估計越穩定；越寬代表資料較不足、需保守看待。' },
  dataSufficiency: { title: '資料充足度', description: '衡量當下可用的可信來源夠不夠多、夠不夠新，足以支撐一個可靠的信任分數。' },
  immutable: { title: '不可竄改', description: '快照一旦寫入即無法修改，用於事後稽核與追溯 HERMES 當時看到的原始資料。' },
  lineage: { title: '血統', description: '資料的來源與處理歷程鏈，可追溯每個數字來自哪個來源、經過哪些步驟得出。' },
  weight: { title: '權重', description: '該來源在計分時的重要程度。信譽高、越新鮮的來源權重越高，對最終信任分影響越大。' },
}

export default function GlossaryTerm({ term, label, compact = false }: { term: GlossaryKey; label?: string; compact?: boolean }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const root = useRef<HTMLSpanElement>(null)
  const entry = GLOSSARY[term]

  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('keydown', close)
    document.addEventListener('mousedown', outside)
    return () => { document.removeEventListener('keydown', close); document.removeEventListener('mousedown', outside) }
  }, [open])

  return (
    <span ref={root} className={`tf-glossary${compact ? ' is-compact' : ''}`} onClick={(event) => event.stopPropagation()}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
        {label ?? entry.title}<i aria-hidden="true">?</i>
      </button>
      {open && <span id={id} className="tf-glossary-popover" role="note"><b>{entry.title}</b><span>{entry.description}</span></span>}
    </span>
  )
}
