import type { ReactNode } from 'react'

interface Props {
  facts: string[]
  inferences: string[]
  marketJudgment: string
}

function Step({ badge, title, children }: { badge: string; title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full bg-tf-accent/20 px-2 py-0.5 text-[0.68rem] font-mono font-semibold text-tf-link">
          {badge}
        </span>
        <h3 className="text-sm font-semibold text-tf-text">{title}</h3>
      </div>
      {children}
    </div>
  )
}

export default function FactsInferenceLadder({ facts, inferences, marketJudgment }: Props) {
  return (
    <div className="flex flex-col gap-3">
      <Step badge="步驟 1/3" title="事實（客觀資料）">
        <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
          {facts.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      </Step>
      <Step badge="步驟 2/3" title="推論（Agent 推理）">
        <ul className="space-y-2 text-sm text-tf-text2">
          {inferences.map((inf, i) => (
            <li key={i} className="whitespace-pre-wrap break-words">
              {inf}
            </li>
          ))}
        </ul>
      </Step>
      <Step badge="步驟 3/3" title="結論 / 市場判斷">
        <p className="text-sm font-medium text-tf-text">{marketJudgment}</p>
      </Step>
    </div>
  )
}
