/** 誠實鐵則揭露：`data.illustrative === true` 的端點資料只來自 fixture，
 * 非即時真實觀測值——UI 必須在顯眼處標「示範資料」，不讓評審誤把
 * fixture 當真實觀測（見 `docs/api/openapi.yaml` peer-metrics/eco-link
 * 說明）。共用小徽章，Peer 比較表與 EcoLink 面板皆用同一個。 */
export default function IllustrativeBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_14%,transparent)] px-2 py-0.5 font-mono text-[0.65rem] font-semibold text-tf-warn"
      title="本資料只來自 fixture，非即時真實觀測值"
      role="note"
    >
      示範資料 · illustrative
    </span>
  )
}
