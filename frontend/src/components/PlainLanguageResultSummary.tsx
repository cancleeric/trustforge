import type { AnalyzeData } from '../lib/types'
import { deriveResultReadiness, type ResultReadiness } from '../lib/resultReadiness'

const readinessCopy: Record<ResultReadiness, { label: string; description: string; tone: string }> = {
  ready: { label: '資料足夠，可進行判讀', description: '目前有足夠的可追溯證據支持這份結論。', tone: 'var(--color-tf-good)' },
  limited: { label: '可以參考，但仍有限制', description: '部分資料不足或可信度偏低，結論需要保守解讀。', tone: 'var(--color-tf-warn)' },
  insufficient: { label: '資料不足，無法可靠判斷', description: '這不是「風險低」，而是目前沒有足夠證據得出可靠結論。', tone: 'var(--color-tf-bad)' },
}

export default function PlainLanguageResultSummary({ data }: { data: AnalyzeData }) {
  const readiness = deriveResultReadiness(data)
  const copy = readinessCopy[readiness]
  const reasons = data.report.key_basis.slice(0, 3)

  return (
    <section className="hermes-result-summary" aria-labelledby="result-summary-title">
      <div className="hermes-result-summary-heading">
        <div>
          <p>先看這裡 · 分析摘要</p>
          <h2 id="result-summary-title">{data.report.market_judgment}</h2>
        </div>
        <div className="hermes-readiness" style={{ borderColor: copy.tone }}>
          <i style={{ background: copy.tone }} />
          <span><b style={{ color: copy.tone }}>{copy.label}</b><small>{copy.description}</small></span>
        </div>
      </div>

      <div className="hermes-result-metrics">
        <div><span>資訊完整度</span><b>{Math.round(data.report.calibrated_confidence * 100)}%</b></div>
        <div><span>可追溯證據</span><b>{data.evidence.length} 筆</b></div>
        <div><span>已知限制</span><b>{data.report.limits.length} 項</b></div>
      </div>

      <div className="hermes-summary-reasons">
        <h3>三個主要原因</h3>
        {reasons.length ? (
          <ol>{reasons.map((reason, index) => <li key={`${reason.claim}-${index}`}><b>{index + 1}</b><span>{reason.claim}</span></li>)}</ol>
        ) : <p>目前沒有足夠的結構化依據可列出主要原因，請查看資料限制。</p>}
      </div>

      <nav className="hermes-result-next" aria-label="分析後下一步">
        <span>接下來可以：</span>
        <a href="#key-basis">查看主要依據</a>
        {data.report.limits.length > 0 && <a href="#known-limits">了解資料限制</a>}
        <a href="#evidence-list">檢查證據來源</a>
        <a href="#technical-analysis">查看完整技術分析</a>
      </nav>
      <p className="hermes-result-disclaimer">信任分數衡量資料與證據的可信程度，不代表價格漲跌機率，也不是投資建議。</p>
    </section>
  )
}
