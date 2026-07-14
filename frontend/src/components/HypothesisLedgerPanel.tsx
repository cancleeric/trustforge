import type { Evidence, HypothesisLedger } from '../lib/types'

function EvidenceRef({ idx, evidence }: { idx: number; evidence: Evidence[] }) {
  const ev = evidence[idx]
  if (!ev) return <li className="text-xs text-tf-muted">E{idx}（證據缺失）</li>
  return (
    <li className="text-xs text-tf-text2">
      <span className="font-semibold text-tf-text">E{idx}</span>{' '}
      <span className="text-tf-muted">{ev.source}（{ev.kind}）</span>：{ev.content_reference}
    </li>
  )
}

export default function HypothesisLedgerPanel({
  ledger,
  evidence,
}: {
  ledger: HypothesisLedger | null | undefined
  evidence: Evidence[]
}) {
  if (!ledger) return null
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-2 text-sm font-semibold text-tf-text">假設驗證：正反方證據對照</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg border p-3" style={{ borderColor: 'var(--color-tf-good)' }}>
          <span className="mb-1 inline-block rounded-full border border-tf-good px-2 py-0.5 text-xs font-semibold text-tf-good">
            支持方（pro）· {ledger.pro.length} 筆
          </span>
          {ledger.pro.length === 0 ? (
            <p className="mt-1 text-xs text-tf-muted">—</p>
          ) : (
            <ul className="mt-1 space-y-1">
              {ledger.pro.map((i) => (
                <EvidenceRef key={i} idx={i} evidence={evidence} />
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-lg border p-3" style={{ borderColor: 'var(--color-tf-bad)' }}>
          <span className="mb-1 inline-block rounded-full border border-tf-bad px-2 py-0.5 text-xs font-semibold text-tf-bad">
            反方（con）· {ledger.con.length} 筆
          </span>
          {ledger.con.length === 0 ? (
            <p className="mt-1 text-xs text-tf-muted">—</p>
          ) : (
            <ul className="mt-1 space-y-1">
              {ledger.con.map((i) => (
                <EvidenceRef key={i} idx={i} evidence={evidence} />
              ))}
            </ul>
          )}
        </div>
      </div>
      <p className="mt-3 rounded border border-tf-warn/50 bg-tf-warn/5 p-2 text-[0.7rem] text-tf-warn">
        侷限說明：{ledger.confidence_limit}
      </p>
    </div>
  )
}
