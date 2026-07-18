import { useEffect, useId, useRef, useState } from 'react'

export type GlossaryKey = 'trustScore' | 'completeness' | 'reputation' | 'corroboration' | 'recency' | 'manipulation' | 'divergence' | 'proxy' | 'rag'

const GLOSSARY: Record<GlossaryKey, { title: string; description: string }> = {
  trustScore: { title: '信任分數', description: '綜合來源信譽、交叉佐證、資料時效與抗操縱能力的可信程度；不是價格漲跌機率。' },
  completeness: { title: '資訊完整度', description: '本次可用資料是否足以支持判讀。完整度低代表證據不足，不代表風險較低。' },
  reputation: { title: '來源信譽', description: '來源過去提供資訊的可靠程度，以及它在不同來源交叉核對後的表現。' },
  corroboration: { title: '交叉佐證', description: '有多少彼此獨立的來源支持同一個說法；不是重複轉載的數量。' },
  recency: { title: '資料時效', description: '資料距離現在有多久。較新的資料通常權重較高，但不一定比較正確。' },
  manipulation: { title: '抗操縱能力', description: '資料抵抗喊單、誇大承諾、協同行為與其他操縱訊號的程度。' },
  divergence: { title: '來源分歧', description: '不同來源對同一問題得出互相衝突的訊號；分歧越大，結論越需要保守解讀。' },
  proxy: { title: '總覽代理值', description: '正式分析尚未完成時，由市場總覽推導的暫時估計；不能取代證據綁定的正式結果。' },
  rag: { title: '相似歷史題目', description: '從過去分析中找相似問題作為參考。它不屬於本次證據，也不參與本次信任評分。' },
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
