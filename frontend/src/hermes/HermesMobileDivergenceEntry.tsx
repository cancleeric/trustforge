import GlossaryTerm from '../components/GlossaryTerm'
import type { SelectedDerivation } from '../lib/hermesData'
import type { CrossSourceSignal } from '../lib/types'
import { useHermesI18n } from './hermesI18n'

interface HermesMobileDivergenceEntryProps {
  derivation: SelectedDerivation
  crossSignal?: CrossSourceSignal | null
  onOpen: () => void
}

export default function HermesMobileDivergenceEntry({ derivation, crossSignal, onOpen }: HermesMobileDivergenceEntryProps) {
  const { t } = useHermesI18n()
  const summary = crossSignal?.summary ?? `${t('divergenceUnit')} · Δ ${derivation.divergence}%`

  return (
    <aside className="hermes-mobile-divergence hermes-clip" aria-label={t('divergence')} style={{ background: derivation.divDim, border: `1px solid ${derivation.divBd}` }}>
      <div className="hermes-mobile-divergence-heading">
        <span aria-hidden="true" style={{ color: derivation.divColor }}>⚠</span>
        <GlossaryTerm term="divergence" label={t('divergence')} compact />
      </div>
      <p>{summary}</p>
      <button type="button" onClick={onOpen}>{t('tapReview')} →</button>
    </aside>
  )
}
