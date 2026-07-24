import type { BasisItem } from '../lib/types'
import AnnotatedText from './AnnotatedText'

export default function KeyBasisList({ items }: { items: BasisItem[] }) {
  if (!items.length) return null
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">關鍵依據（判斷 → 證據對照）</h3>
      <ul className="flex flex-col gap-3">
        {items.map((item, i) => (
          <li key={i} className="border-l-2 border-tf-accent pl-3">
            <p className="text-sm text-tf-text"><AnnotatedText text={item.claim} /></p>
            <p className="mt-0.5 text-xs text-tf-muted"><AnnotatedText text={item.explanation} compact /></p>
            {item.evidence_idx.length > 0 && (
              <p className="mt-1 flex flex-wrap gap-1">
                {item.evidence_idx.map((idx) => (
                  <a
                    key={idx}
                    href={`#evidence-${idx}`}
                    className="tf-num rounded border border-tf-border px-1.5 py-0.5 text-[0.68rem] text-tf-link no-underline hover:border-tf-accent"
                  >
                    E{idx}
                  </a>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
