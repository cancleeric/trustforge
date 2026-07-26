import { useEffect, useState } from 'react'
import { rememberHermesOnboarding } from '../lib/beginnerExperience'
import { useHermesI18n } from './hermesI18n'

export default function HermesOnboarding({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useHermesI18n()
  const [step, setStep] = useState(0)

  const steps = [
    { eyebrow: t('ob1Eyebrow'), title: t('ob1Title'), body: t('ob1Body'), hint: t('ob1Hint') },
    { eyebrow: t('ob2Eyebrow'), title: t('ob2Title'), body: t('ob2Body'), hint: t('ob2Hint') },
    { eyebrow: t('ob3Eyebrow'), title: t('ob3Title'), body: t('ob3Body'), hint: t('ob3Hint') },
  ]

  useEffect(() => {
    if (open) setStep(0)
  }, [open])

  if (!open) return null
  const item = steps[step]
  const finish = () => {
    rememberHermesOnboarding()
    onClose()
  }

  return (
    <div className="hermes-onboarding" role="dialog" aria-modal="true" aria-labelledby="hermes-onboarding-title">
      <button className="hermes-onboarding-scrim" type="button" aria-label={t('obCloseAria')} onClick={finish} />
      <section className="hermes-onboarding-card">
        <div className="hermes-onboarding-eyebrow">{item.eyebrow}</div>
        <h2 id="hermes-onboarding-title">{item.title}</h2>
        <p>{item.body}</p>
        <div className="hermes-onboarding-hint">💡 {item.hint}</div>
        {step === steps.length - 1 && (
          <a href="/help" className="hermes-onboarding-help-link" style={{ color: 'var(--color-tf-link)', fontSize: '0.8125rem' }}>
            {t('obFullGuideLink')}
          </a>
        )}
        <div className="hermes-onboarding-progress" aria-label={t('obProgressAriaTemplate', { step: step + 1, total: steps.length })}>
          {steps.map((_, index) => <i key={index} className={index === step ? 'is-active' : ''} />)}
        </div>
        <footer>
          <button type="button" className="is-quiet" onClick={finish}>{t('obSkip')}</button>
          <div>
            {step > 0 && <button type="button" className="is-secondary" onClick={() => setStep((value) => value - 1)}>{t('obPrev')}</button>}
            <button type="button" className="is-primary" onClick={() => step === steps.length - 1 ? finish() : setStep((value) => value + 1)}>
              {step === steps.length - 1 ? t('obFinish') : t('obNext')}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
