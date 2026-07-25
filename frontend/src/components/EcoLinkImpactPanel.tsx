import type { EcoLinkImpactPath } from '../lib/types'
import { safeHref } from '../lib/safeHref'
import IllustrativeBadge from './IllustrativeBadge'

/** EcoLink 影響路徑面板（模組③ Wave 3）：`verdict === 'insufficient_data'`
 * 時顯示「資料不足，無法判定」，不假裝「沒有影響」；措辭一律「可能
 * 相關」，絕不出現「導致」「因此」等因果字眼（見 `docs/api/openapi.yaml`
 * eco-link 說明）。每條路徑附 confidence + 官方來源連結，方便自行查證。 */

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  return (
    <span
      className="inline-flex items-center rounded-full border border-tf-link px-2 py-0.5 font-mono text-[0.65rem] font-semibold text-tf-link"
      title="confidence 分數（非因果證明）"
    >
      confidence {pct}%
    </span>
  )
}

function ImpactPathCard({ path }: { path: EcoLinkImpactPath }) {
  // 安全鐵則：`official_source_url` 來自 fixture/後端，塞進 `<a href>`
  // 前一律先過 `safeHref`（只放行 http/https，擋 javascript:/data: 等
  // 可執行 scheme）。`safeHref` 回 `null` 時不渲染成可點連結，改顯示
  // 純文字，不得靜默 fallback 成其他目的地（見 `lib/safeHref.ts`）。
  const href = safeHref(path.official_source_url)
  return (
    <li className="rounded-lg border border-tf-border bg-tf-bg p-3" data-testid="impact-path">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-mono text-xs text-tf-text">{path.path.join(' → ')}</p>
        <ConfidenceBadge confidence={path.confidence} />
      </div>
      <p className="mt-1 text-xs text-tf-muted">方向：{path.direction}</p>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-1 inline-block break-all text-xs text-tf-link underline hover:no-underline"
        >
          官方來源
        </a>
      ) : (
        <span className="mt-1 inline-block break-all text-xs text-tf-muted">官方來源（連結格式無效）</span>
      )}
    </li>
  )
}

export default function EcoLinkImpactPanel({
  verdict,
  message,
  impactPaths,
}: {
  verdict: 'possible_relation' | 'insufficient_data'
  message: string
  impactPaths: EcoLinkImpactPath[]
}) {
  if (verdict === 'insufficient_data') {
    return (
      <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4 text-sm text-tf-muted" role="status">
        <div className="mb-2">
          <IllustrativeBadge />
        </div>
        資料不足，無法判定。
      </div>
    )
  }
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">EcoLink 影響路徑</p>
        <IllustrativeBadge />
      </div>
      <p className="mb-3 text-sm text-tf-text2">{message}</p>
      <ul className="flex flex-col gap-2">
        {impactPaths.map((path) => (
          <ImpactPathCard key={path.event_id} path={path} />
        ))}
      </ul>
    </div>
  )
}
