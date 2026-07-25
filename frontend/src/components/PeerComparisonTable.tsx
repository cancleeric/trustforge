import type { PeerComparisonEntry, PeerMetricsSnapshot } from '../lib/types'
import { formatMetricValue } from '../lib/peerMetricsFormat'
import IllustrativeBadge from './IllustrativeBadge'

/** 同層 peer 比較表（模組③ Wave 3）：TPS/TVL/Gas/活躍度並列。
 * `comparable: false` 的 peer 顯示「無法比較：{reason}」，不留白補 0；
 * 缺值欄位一律顯示「—」（見 `docs/api/openapi.yaml` peer-metrics 說明）。
 * desktop 用表格，375/390 手機寬度改用卡片 fallback，避免橫向溢位。 */

function activitySummary(snapshot: PeerMetricsSnapshot): string {
  const breakdown = snapshot.activity_breakdown
  if (!breakdown || Object.keys(breakdown).length === 0) return '—'
  return Object.entries(breakdown)
    .map(([key, metric]) => `${key} ${formatMetricValue(metric)}`)
    .join('、')
}

function AssetRow({
  label,
  snapshot,
  comparable,
  reason,
}: {
  label: string
  snapshot: PeerMetricsSnapshot | null
  comparable: boolean
  reason: string | null
}) {
  if (!comparable || !snapshot) {
    return (
      <>
        <tr className="border-b border-tf-border last:border-0" data-testid="peer-row">
          <th scope="row" className="py-2 pr-3 text-left align-top font-mono text-xs font-semibold text-tf-text">
            {label}
          </th>
          <td colSpan={4} className="py-2 text-xs text-tf-warn" role="note">
            無法比較：{reason ?? '原因未知'}
          </td>
        </tr>
      </>
    )
  }
  return (
    <tr className="border-b border-tf-border last:border-0" data-testid="peer-row">
      <th scope="row" className="py-2 pr-3 text-left align-top font-mono text-xs font-semibold text-tf-text">
        {label}
      </th>
      <td className="py-2 pr-3 text-xs text-tf-text2">{formatMetricValue(snapshot.observed_tps)}</td>
      <td className="py-2 pr-3 text-xs text-tf-text2">{formatMetricValue(snapshot.tvl)}</td>
      <td className="py-2 pr-3 text-xs text-tf-text2">{formatMetricValue(snapshot.gas_fee)}</td>
      <td className="py-2 text-xs text-tf-text2">{activitySummary(snapshot)}</td>
    </tr>
  )
}

function AssetCard({
  label,
  snapshot,
  comparable,
  reason,
}: {
  label: string
  snapshot: PeerMetricsSnapshot | null
  comparable: boolean
  reason: string | null
}) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-bg p-3" data-testid="peer-card">
      <p className="font-mono text-xs font-semibold text-tf-text">{label}</p>
      {!comparable || !snapshot ? (
        <p className="mt-1 text-xs text-tf-warn" role="note">
          無法比較：{reason ?? '原因未知'}
        </p>
      ) : (
        <dl className="mt-1 grid grid-cols-2 gap-x-2 gap-y-1 text-xs text-tf-text2">
          <div>
            <dt className="text-tf-muted">TPS</dt>
            <dd>{formatMetricValue(snapshot.observed_tps)}</dd>
          </div>
          <div>
            <dt className="text-tf-muted">TVL</dt>
            <dd>{formatMetricValue(snapshot.tvl)}</dd>
          </div>
          <div>
            <dt className="text-tf-muted">Gas</dt>
            <dd>{formatMetricValue(snapshot.gas_fee)}</dd>
          </div>
          <div>
            <dt className="text-tf-muted">活躍度</dt>
            <dd>{activitySummary(snapshot)}</dd>
          </div>
        </dl>
      )}
    </div>
  )
}

export default function PeerComparisonTable({
  snapshot,
  peers,
}: {
  snapshot: PeerMetricsSnapshot
  peers: PeerComparisonEntry[]
}) {
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">同層比較</p>
        <IllustrativeBadge />
      </div>

      {/* desktop：橫向表格；sm 以下隱藏，避免固定欄寬造成橫向溢位。 */}
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-tf-border text-xs font-semibold text-tf-muted">
              <th scope="col" className="py-2 pr-3">資產</th>
              <th scope="col" className="py-2 pr-3">TPS</th>
              <th scope="col" className="py-2 pr-3">TVL</th>
              <th scope="col" className="py-2 pr-3">Gas</th>
              <th scope="col" className="py-2">活躍度</th>
            </tr>
          </thead>
          <tbody>
            <AssetRow label={`${snapshot.asset_id}（本體）`} snapshot={snapshot} comparable reason={null} />
            {peers.map((peer) => (
              <AssetRow
                key={peer.asset_id}
                label={peer.asset_id}
                snapshot={peer.snapshot}
                comparable={peer.comparable}
                reason={peer.reason}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* mobile fallback（375/390 寬度）：卡片堆疊，不用表格。 */}
      <div className="flex flex-col gap-2 sm:hidden">
        <AssetCard label={`${snapshot.asset_id}（本體）`} snapshot={snapshot} comparable reason={null} />
        {peers.map((peer) => (
          <AssetCard
            key={peer.asset_id}
            label={peer.asset_id}
            snapshot={peer.snapshot}
            comparable={peer.comparable}
            reason={peer.reason}
          />
        ))}
      </div>

      {peers.length === 0 && (
        <p className="mt-2 text-xs text-tf-muted">目前無同層 peer 可比較。</p>
      )}
    </div>
  )
}
