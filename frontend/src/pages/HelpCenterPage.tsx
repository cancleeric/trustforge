import { HELP_CENTER_GLOSSARY } from '../lib/glossaryCatalog'
import { useHermesI18n } from '../hermes/hermesI18n'
// 說明中心（/help，R2 深色艦橋設計稿新頁）：彙整名詞解釋、5 階段管線原理、
// 信任分數色帶與常見問題。內容取自設計稿逐字稿（見 docs/design/hermes-r2-darkbridge/
// HERMES Help Center.dc.html），純靜態頁、無需後端資料，故不比照其他頁面
// 走 API loading/error 狀態機。

type Stage = { num: string; label: string; desc: string; highlight?: boolean }
type Band = { range: string; label: string; color: string; bg: string; border: string; desc: string }
type Faq = { q: string; a: string }

type Translate = ReturnType<typeof useHermesI18n>['t']

function useHelpContent(t: Translate) {
  // N58：這頁原本把全部文案硬寫成中文常數，en 語系整頁不翻。改成從 t() 取，
  // 資料形狀不動（Stage/Band/Faq），只把字串來源換成 i18n。
  const STAGES: Stage[] = [
    { num: '01', label: t('helpStage1'), desc: t('helpStage1Desc') },
    { num: '02', label: t('helpStage2'), desc: t('helpStage2Desc') },
    { num: '03', label: t('helpStage3'), desc: t('helpStage3Desc') },
    { num: '04', label: t('helpStage4'), desc: t('helpStage4Desc') },
    { num: '05', label: t('helpStage5'), desc: t('helpStage5Desc'), highlight: true },
  ]
  const BANDS: Band[] = [
    { range: '0–39', label: t('helpBandLow'), color: 'var(--color-tf-bad)', bg: 'color-mix(in srgb, var(--color-tf-bad) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-bad) 40%, transparent)', desc: t('helpBandLowDesc') },
    { range: '40–69', label: t('helpBandMid'), color: 'var(--color-tf-warn)', bg: 'color-mix(in srgb, var(--color-tf-warn) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-warn) 40%, transparent)', desc: t('helpBandMidDesc') },
    { range: '70–100', label: t('helpBandHigh'), color: 'var(--color-tf-green)', bg: 'color-mix(in srgb, var(--color-tf-green) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)', desc: t('helpBandHighDesc') },
  ]
  // 逐條列出而不用樣板字串組 key：i18n 的 key 是 union 型別，組出來的
  // string 過不了型別檢查，也就失去「key 打錯編譯期就爆」的保護。
  const FAQS: Faq[] = [
    { q: t('helpFaq1Q'), a: t('helpFaq1A') },
    { q: t('helpFaq2Q'), a: t('helpFaq2A') },
    { q: t('helpFaq3Q'), a: t('helpFaq3A') },
    { q: t('helpFaq4Q'), a: t('helpFaq4A') },
    { q: t('helpFaq5Q'), a: t('helpFaq5A') },
    { q: t('helpFaq6Q'), a: t('helpFaq6A') },
  ]
  return { STAGES, BANDS, FAQS }
}

function AnchorNav({ t }: { t: Translate }) {
  const items: { href: string; label: string }[] = [
    { href: '#glossary', label: t('helpNavGlossary') },
    { href: '#how', label: t('helpNavHow') },
    { href: '#score', label: t('helpNavScore') },
    { href: '#faq', label: t('helpNavFaq') },
  ]
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <a
          key={item.href}
          href={item.href}
          className="rounded-[7px] border border-tf-border bg-tf-card px-3.5 py-1.5 text-xs text-tf-muted no-underline hover:text-tf-text"
        >
          {item.label}
        </a>
      ))}
    </div>
  )
}

export default function HelpCenterPage() {
  const { t } = useHermesI18n()
  const { STAGES, BANDS, FAQS } = useHelpContent(t)
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 70% -10%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 60%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">{t('helpKicker')}</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">{t('helpTitle')}</h1>
        <p className="mt-1 max-w-2xl text-sm text-tf-text2">
          {t('helpIntro')}
        </p>
      </div>

      <AnchorNav t={t} />

      <section id="glossary" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">{t('helpGlossaryHead')}</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">{t('helpGlossarySub')}</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-tf-border text-xs uppercase tracking-wide text-tf-muted">
                <th className="px-2 py-2 font-medium">{t('helpColTerm')}</th>
                <th className="px-2 py-2 font-medium">{t('helpColPlain')}</th>
                <th className="px-2 py-2 font-medium">{t('helpColWhere')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-tf-border">
              {HELP_CENTER_GLOSSARY.map((g) => (
                <tr key={g.term_id}>
                  <td className="px-2 py-3 align-top">
                    <p className="text-sm font-semibold text-tf-text">{g.label}</p>
                    <p className="text-xs text-tf-muted">{g.aliases[0] ?? ''}</p>
                  </td>
                  <td className="px-2 py-3 align-top text-xs leading-relaxed text-tf-text2">{g.description}</td>
                  <td className="px-2 py-3 align-top text-xs leading-relaxed text-tf-muted">{g.where}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section id="how" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">{t('helpHowHead')}</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">{t('helpHowSub')}</p>
        <div className="flex flex-col gap-2">
          {STAGES.map((s) => (
            <div key={s.num} className={`flex items-start gap-3 rounded-[9px] border p-3 ${s.highlight ? 'border-tf-warn/50 bg-[color-mix(in_srgb,var(--color-tf-warn)_10%,transparent)]' : 'border-tf-border bg-tf-bg'}`}>
              <span
                className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] text-xs font-bold"
                style={s.highlight
                  ? { background: 'color-mix(in srgb, var(--color-tf-warn) 20%, transparent)', border: '1px solid var(--color-tf-warn)', color: 'var(--color-tf-warn)' }
                  : { background: 'color-mix(in srgb, var(--color-tf-link) 15%, transparent)', border: '1px solid var(--color-tf-link)', color: 'var(--color-tf-link)' }}
              >
                {s.num}
              </span>
              <div>
                <p className="text-sm font-semibold text-tf-text">{s.label}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-tf-muted">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section id="score" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">{t('helpScoreHead')}</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">{t('helpScoreSub')}</p>
        <p className="mb-4 max-w-3xl text-sm leading-relaxed text-tf-text2">
          {t('helpScoreIntroPre')}
          <span className="font-semibold text-tf-text">{t('helpScoreIntroBold')}</span>
          {t('helpScoreIntroPost')}
        </p>
        <div
          className="mb-2 h-4 rounded-lg"
          style={{ background: 'linear-gradient(90deg, var(--color-tf-bad) 0%, var(--color-tf-bad) 39%, var(--color-tf-warn) 40%, var(--color-tf-warn) 69%, var(--color-tf-green) 70%, var(--color-tf-green) 100%)' }}
        />
        <div className="mb-5 flex justify-between font-mono text-[10px] text-tf-muted">
          <span>0</span><span>39</span><span>40</span><span>69</span><span>70</span><span>100</span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {BANDS.map((b) => (
            <div key={b.range} className="rounded-[9px] border p-4" style={{ background: b.bg, borderColor: b.border }}>
              <div className="mb-2 flex items-center gap-2">
                <span className="tf-num text-lg font-bold" style={{ color: b.color }}>{b.range}</span>
                <span className="text-sm font-semibold" style={{ color: b.color }}>{b.label}</span>
              </div>
              <p className="text-xs leading-relaxed text-tf-muted">{b.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="faq" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">{t('helpFaqHead')}</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">{t('helpFaqSub')}</p>
        <div className="flex flex-col gap-3">
          {FAQS.map((f) => (
            <div key={f.q} className="rounded-[10px] border border-tf-border bg-tf-bg p-4">
              <div className="mb-2 flex items-start gap-2">
                <span className="flex-shrink-0 text-sm font-bold text-tf-link">Q</span>
                <span className="text-sm font-semibold text-tf-text">{f.q}</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="flex-shrink-0 text-sm font-bold text-tf-warn">A</span>
                <span className="text-xs leading-relaxed text-tf-muted">{f.a}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-tf-border pt-4">
          <span className="text-xs text-tf-muted">
            {t('helpFooterPre')}<span className="text-tf-link">{t('helpFooterLink')}</span>{t('helpFooterPost')}
          </span>
          <a
            href="/analyze"
            className="rounded-[7px] px-4 py-2.5 text-xs font-bold tracking-wide text-tf-bg no-underline"
            style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)' }}
          >
            {t('helpFooterCta')}
          </a>
        </div>
      </section>
    </main>
  )
}
