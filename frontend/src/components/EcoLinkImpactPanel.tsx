import type { EcoLinkImpactPath } from '../lib/types'
import { safeHref } from '../lib/safeHref'
import IllustrativeBadge from './IllustrativeBadge'
import { useHermesI18n } from '../hermes/hermesI18n'

/** EcoLink 影響路徑面板（模組③ Wave 3）：`verdict === 'insufficient_data'`
 * 時顯示「資料不足，無法判定」，不假裝「沒有影響」；措辭一律「可能
 * 相關」，絕不出現「導致」「因此」等因果字眼（見 `docs/api/openapi.yaml`
 * eco-link 說明）。每條路徑附 confidence + 官方來源連結，方便自行查證。 */

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const { t } = useHermesI18n()
  const pct = Math.round(confidence * 100)
  return (
    <span
      className="inline-flex items-center rounded-full border border-tf-link px-2 py-0.5 font-mono text-[0.65rem] font-semibold text-tf-link"
      title={t('elipConfidenceTitle')}
    >
      confidence {pct}%
    </span>
  )
}

function ImpactPathCard({ path }: { path: EcoLinkImpactPath }) {
  const { t } = useHermesI18n()
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
      <p className="mt-1 text-xs text-tf-muted">{t('elipDirectionPrefix')}{path.direction}</p>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          // N36: 實測 zh-TW 48x16、en 108x16，高度只有 16px（text-xs 行高），
          // 低於 24px 最小點擊目標。改 inline-flex + min-h 只長高不動寬，
          // break-all 仍作用在唯一的文字子項上。
          className="mt-1 inline-flex min-h-[24px] items-center break-all text-xs text-tf-link underline hover:no-underline"
        >
          {t('elipOfficialSource')}
        </a>
      ) : (
        <span className="mt-1 inline-block break-all text-xs text-tf-muted">{t('elipInvalidLink')}</span>
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
  const { t } = useHermesI18n()
  if (verdict === 'insufficient_data') {
    return (
      <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4 text-sm text-tf-muted" role="status">
        <div className="mb-2">
          <IllustrativeBadge />
        </div>
        {t('elipInsufficientData')}
      </div>
    )
  }
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">{t('elipTitle')}</p>
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
