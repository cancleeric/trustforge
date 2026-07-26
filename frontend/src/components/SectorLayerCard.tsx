import type { AssetContext, AssetLayer, TokenRole } from '../lib/types'
import AnnotatedText from './AnnotatedText'
import { useHermesI18n, type MessageKey } from '../hermes/hermesI18n'

const UNKNOWN = 'unknown'

const LAYER_LABEL_KEY: Record<AssetLayer, MessageKey | null> = {
  layer_1: null,
  layer_2: null,
  app: null,
  protocol: null,
  token: null,
  offchain: null,
  unknown: 'slcLayerUnknown',
}
const LAYER_LABEL_LITERAL: Record<AssetLayer, string> = {
  layer_1: 'Layer 1',
  layer_2: 'Layer 2',
  app: 'App',
  protocol: 'Protocol',
  token: 'Token',
  offchain: 'Off-chain',
  unknown: '',
}

const TOKEN_ROLE_LABEL_KEY: Record<TokenRole, MessageKey | null> = {
  gas: null,
  governance: 'slcRoleGovernance',
  utility: 'slcRoleUtility',
  staking: 'slcRoleStaking',
  stable: 'slcRoleStable',
  lp: null,
  wrapped: 'slcRoleWrapped',
  meme: null,
  unknown: 'slcRoleUnknown',
}
const TOKEN_ROLE_LABEL_LITERAL: Record<TokenRole, string> = {
  gas: 'Gas', governance: '', utility: '', staking: '', stable: '', lp: 'LP', wrapped: '', meme: 'Meme', unknown: '',
}

function layerLabel(layer: AssetLayer, t: (key: MessageKey) => string): string {
  const key = LAYER_LABEL_KEY[layer]
  return key ? t(key) : LAYER_LABEL_LITERAL[layer]
}

function tokenRoleLabel(role: TokenRole, t: (key: MessageKey) => string): string {
  const key = TOKEN_ROLE_LABEL_KEY[role]
  return key ? t(key) : TOKEN_ROLE_LABEL_LITERAL[role]
}

/** 白話關聯說明——只在有足夠已知欄位（sector/settlement_chain）時才組句，
 * 缺值一律不猜、直接跳過該句，避免拼出「依附於 unknown」這種假訊息。 */
function relationSummary(context: AssetContext, t: (key: MessageKey, params?: Record<string, string | number>) => string): string | null {
  const chain = context.settlement_chain
  if (!chain || chain === UNKNOWN) return null
  const chainLabel = chain.charAt(0).toUpperCase() + chain.slice(1)
  return t('slcRelationSummaryTemplate', { chain: chainLabel, symbol: context.symbol })
}

export default function SectorLayerCard({ context }: { context: AssetContext }) {
  const { t } = useHermesI18n()
  const settlementChain = context.settlement_chain ?? UNKNOWN
  const gasToken = context.gas_token ?? UNKNOWN
  const dependencies = context.dependencies ?? []
  const gasMismatch =
    gasToken !== UNKNOWN && gasToken.toUpperCase() !== context.symbol.toUpperCase()
  const summary = relationSummary(context, t)

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
          [{layerLabel(context.layer, t)}]
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="font-semibold text-tf-muted">{t('slcSettlementChain')}</dt>
          <dd className="mt-0.5 text-tf-text">{settlementChain}</dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">{t('slcGasToken')}</dt>
          <dd className="mt-0.5 flex items-center gap-1 text-tf-text">
            {gasToken}
            {gasMismatch && (
              <span
                className="inline-flex items-center gap-1 rounded-full border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_14%,transparent)] px-1.5 py-0.5 text-[0.68rem] font-semibold text-tf-warn"
                title={t('slcGasFeeNoteTemplate', { gasToken })}
              >
                &#9888; <AnnotatedText text={t('slcGasFeeNoteTemplate', { gasToken })} compact />
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">{t('slcTokenRole')}</dt>
          <dd className="mt-0.5 text-tf-text">{tokenRoleLabel(context.token_role, t)}</dd>
        </div>
        <div>
          <dt className="font-semibold text-tf-muted">{t('slcEcosystem')}</dt>
          <dd className="mt-0.5 text-tf-text">{context.ecosystem ?? UNKNOWN}</dd>
        </div>
      </dl>

      <div className="mt-3">
        <p className="text-xs font-semibold text-tf-muted">{t('slcDependencies')}</p>
        {dependencies.length === 0 ? (
          <p className="mt-0.5 text-xs text-tf-muted">{t('slcNoDependencies')}</p>
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

      {summary && (
        <p className="mt-3 text-xs leading-relaxed text-tf-text2">
          <AnnotatedText text={summary} compact />
        </p>
      )}
    </div>
  )
}
