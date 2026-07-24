import type { AssetContext } from '../lib/types'

const UNKNOWN = 'unknown'

const LAYER_LABEL: Record<string, string> = {
  layer_1: 'Layer 1',
  layer_2: 'Layer 2',
  app: 'App',
  protocol: 'Protocol',
  token: 'Token',
  offchain: 'Off-chain',
  unknown: 'Layer 未知',
}

const TOKEN_ROLE_LABEL: Record<string, string> = {
  gas: 'Gas',
  governance: '治理',
  utility: '功能性',
  staking: '質押',
  stable: '穩定幣',
  lp: 'LP',
  wrapped: '包裝資產',
  meme: 'Meme',
  unknown: '未知',
}

function layerLabel(layer: string): string {
  return LAYER_LABEL[layer] ?? layer
}

function tokenRoleLabel(role: string): string {
  return TOKEN_ROLE_LABEL[role] ?? role
}

/** 白話關聯說明——只在有足夠已知欄位（sector/settlement_chain）時才組句，
 * 缺值一律不猜、直接跳過該句，避免拼出「依附於 unknown」這種假訊息。 */
function relationSummary(context: AssetContext): string | null {
  const chain = context.settlement_chain
  if (!chain || chain === UNKNOWN) return null
  const chainLabel = chain.charAt(0).toUpperCase() + chain.slice(1)
  return `依附於 ${chainLabel}，${chainLabel} 生態熱度上升可能帶動 ${context.symbol} 的關注與流量；手續費與 ${chainLabel} 的 gas 機制相關。`
}

export default function SectorLayerCard({ context }: { context: AssetContext }) {
  const settlementChain = context.settlement_chain ?? UNKNOWN
  const gasToken = context.gas_token ?? UNKNOWN
  const dependencies = context.dependencies ?? []
  const gasMismatch = gasToken !== UNKNOWN && gasToken !== context.symbol
  const summary = relationSummary(context)

  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-base font-bold text-tf-text">{context.symbol}</span>
        <span
          className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold"
          style={{
            color: 'var(--color-tf-link)',
            borderColor: 'var(--color-tf-link)',
            backgroundColor: 'color-mix(in srgb, var(--color-tf-link) 14%, transparent)',
          }}
        >
          [{layerLabel(context.layer)}]
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="font-semibold text-tf-muted">結算鏈</dt>
          <dd className="mt-0.5 text-tf-text">{settlementChain}</dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">Gas 代幣</dt>
          <dd className="mt-0.5 flex items-center gap-1 text-tf-text">
            {gasToken}
            {gasMismatch && (
              <span
                className="inline-flex items-center gap-1 rounded-full border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_14%,transparent)] px-1.5 py-0.5 text-[0.68rem] font-semibold text-tf-warn"
                title={`轉帳手續費需 ${gasToken}`}
              >
                &#9888; 轉帳手續費需 {gasToken}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">Token 角色</dt>
          <dd className="mt-0.5 text-tf-text">{tokenRoleLabel(context.token_role)}</dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">生態系</dt>
          <dd className="mt-0.5 text-tf-text">{context.ecosystem ?? UNKNOWN}</dd>
        </div>
      </dl>

      <div className="mt-3">
        <p className="text-xs font-semibold text-tf-muted">上下游關聯</p>
        {dependencies.length === 0 ? (
          <p className="mt-0.5 text-xs text-tf-muted">無已知上下游關聯</p>
        ) : (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {dependencies.map((dep) => (
              <li
                key={dep}
                className="rounded border border-tf-border bg-tf-bg px-1.5 py-0.5 text-[0.68rem] text-tf-text2"
              >
                {dep}
              </li>
            ))}
          </ul>
        )}
      </div>

      {summary && <p className="mt-3 text-xs leading-relaxed text-tf-text2">{summary}</p>}
    </div>
  )
}
