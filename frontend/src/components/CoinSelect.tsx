import { COIN_POOL } from '../lib/constants'

export default function CoinSelect({
  id, label = '幣種', value, onChange,
}: {
  id: string
  label?: string
  value: string
  onChange: (coin: string) => void
}) {
  return (
    <div className="tf-coin-select" id={id}>
      <span className="mb-1 block text-xs font-semibold text-tf-muted">{label}</span>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={label}>
        {COIN_POOL.map((coin) => {
          const selected = coin === value
          return (
            <button
              key={coin}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(coin)}
              className={`min-w-12 rounded border px-3 py-1.5 font-mono text-sm font-semibold transition ${selected
                ? 'border-tf-accent bg-tf-accent text-white shadow-[0_0_12px_rgba(77,216,224,.24)]'
                : 'border-tf-border bg-tf-bg text-tf-text2 hover:border-tf-link hover:text-tf-text'}`}
            >
              {coin}
            </button>
          )
        })}
      </div>
    </div>
  )
}
