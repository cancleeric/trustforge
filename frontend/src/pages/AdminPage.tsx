// 管理控制台（/admin，PR-4，計劃 §4）：token 閘 → 設定表單（每日 cap /
// Bedrock 開關 / live token 輪替）→ 誠實顯示（生效值+來源徽章）→ 審計檢視。
//
// 安全紀律（token 儲存策略，harper 掃描重點——詳見 `lib/adminConsole.ts`
// 檔頭）：admin token 主存 React state（記憶體），輔以 sessionStorage
// （同分頁 reload UX，分頁關即清），**絕不用 localStorage**、絕不進 URL。
// live token 明文只在「產生新 token」成功後的本次畫面一次性顯示，離開
// 即不可再取（後端只存 hash+末4碼，本來也拿不回來）。

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  clearSessionToken,
  formatAuditValue,
  generateLiveToken,
  loadSessionToken,
  saveSessionToken,
  sourceLabel,
  validateCapInput,
  ADMIN_CAP_MAX_USD,
  ADMIN_CAP_MIN_USD,
} from '../lib/adminConsole'
import {
  getAdminAudit,
  getAdminBackendProviders,
  getAdminConfig,
  putAdminConfig,
  setAdminBackendProvider,
  setAllAdminBackendProviders,
} from '../lib/endpoints'
import type {
  AdminAuditRecord,
  AdminBackendProvidersData,
  AdminConfigChanges,
  AdminConfigData,
  BackendProvider,
  BackendProviderKey,
} from '../lib/types'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useHermesI18n, type MessageKey } from '../hermes/hermesI18n'

// ── 小元件 ──────────────────────────────────────────────────────────────

function SourceBadge({ source }: { source: string }) {
  const tone =
    source === 'config'
      ? 'border-tf-link/50 text-tf-link'
      : source === 'env'
        ? 'border-tf-warn/60 text-tf-warn'
        : 'border-tf-muted/50 text-tf-muted'
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-xs ${tone}`}>
      {sourceLabel(source)}
    </span>
  )
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h2 className="mb-3 text-sm font-semibold text-tf-text">{title}</h2>
      {children}
    </section>
  )
}

function AuditTable({ records }: { records: AdminAuditRecord[] }) {
  const { t } = useHermesI18n()
  if (records.length === 0) {
    return <p className="text-sm text-tf-muted">{t('adminNoAuditRecords')}</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[560px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">{t('adminAuditTime')}</th>
            <th className="px-3 py-2 font-medium">{t('adminAuditActor')}</th>
            <th className="px-3 py-2 font-medium">{t('adminAuditField')}</th>
            <th className="px-3 py-2 font-medium">{t('adminAuditChange')}</th>
            <th className="tf-num px-3 py-2 text-right font-medium">{t('adminAuditVersion')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {records.map((rec, i) => (
            <tr key={`${rec.ts ?? 'no-ts'}-${i}`}>
              <td className="px-3 py-2 align-top text-tf-text2">{rec.ts ?? '—'}</td>
              <td className="px-3 py-2 align-top text-tf-text2">{rec.actor ?? '—'}</td>
              <td className="px-3 py-2 align-top text-tf-text2">
                {rec.changes.length === 0 && '—'}
                {rec.changes.map((c, j) => (
                  <div key={j}>{c.field}</div>
                ))}
              </td>
              <td className="px-3 py-2 align-top text-tf-text2">
                {rec.changes.length === 0 && '—'}
                {rec.changes.map((c, j) => (
                  <div key={j}>
                    {formatAuditValue(c.field, c.old)} → {formatAuditValue(c.field, c.new)}
                  </div>
                ))}
              </td>
              <td className="tf-num px-3 py-2 text-right align-top text-tf-text2">
                {rec.version_from ?? '—'} → {rec.version_to ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 主頁面 ──────────────────────────────────────────────────────────────

const BTN_PRIMARY =
  'rounded-md bg-tf-accent/20 px-3 py-1.5 text-sm font-semibold text-tf-link transition hover:bg-tf-accent/30 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_PLAIN =
  'rounded-md border border-tf-border px-3 py-1.5 text-sm text-tf-text2 transition hover:text-tf-link disabled:cursor-not-allowed disabled:opacity-50'

function backendProviderLabels(t: (key: MessageKey) => string): Record<BackendProviderKey, string> {
  return {
    memory: t('adminBackendMemory'),
    policy: t('adminBackendPolicy'),
    eval: t('adminBackendEval'),
    llm: t('adminBackendLlm'),
    gateway: t('adminBackendGateway'),
    observability: t('adminBackendObservability'),
    upgrade: t('adminBackendUpgrade'),
  }
}

export default function AdminPage() {
  const { t } = useHermesI18n()
  const BACKEND_PROVIDER_LABELS = backendProviderLabels(t)
  // token 主存 React state；初始值嘗試從 sessionStorage 撈（同分頁 reload）
  const [token, setToken] = useState<string | null>(() => loadSessionToken())
  const [tokenInput, setTokenInput] = useState('')
  const [gateError, setGateError] = useState<{ code: string; message: string } | null>(null)
  const [checking, setChecking] = useState(false)

  const [config, setConfig] = useState<AdminConfigData | null>(null)
  const [audit, setAudit] = useState<AdminAuditRecord[] | null>(null)
  const [auditError, setAuditError] = useState<{ code: string; message: string } | null>(null)
  const [backendProviders, setBackendProviders] = useState<AdminBackendProvidersData | null>(null)
  const [backendError, setBackendError] = useState<{ code: string; message: string } | null>(null)

  const [capInput, setCapInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string[]>([])
  const [saveError, setSaveError] = useState<{ code: string; message: string } | null>(null)
  const [backendSaving, setBackendSaving] = useState(false)
  const [confirmBedrockOn, setConfirmBedrockOn] = useState(false)
  // live token 明文一次性顯示（僅本次 render 週期的 state，不落任何儲存）
  const [freshLiveToken, setFreshLiveToken] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // PUT/重載共用：任何成功拿到的最新 config 都同步 cap 輸入框
  const applyConfig = useCallback((data: AdminConfigData) => {
    setConfig(data)
    setCapInput(data.daily_cap_usd.config !== null ? String(data.daily_cap_usd.config) : '')
  }, [])

  const refreshAudit = useCallback(
    (activeToken: string, signal?: AbortSignal) => {
      getAdminAudit(activeToken, signal).then((res) => {
        if (signal?.aborted) return
        if (res.ok) {
          setAudit(res.data.records)
          setAuditError(null)
        } else {
          setAudit(null)
          setAuditError(res.error)
        }
      })
    },
    [],
  )

  const refreshBackendProviders = useCallback((activeToken: string, signal?: AbortSignal) => {
    getAdminBackendProviders(activeToken, signal).then((res) => {
      if (signal?.aborted) return
      if (res.ok) {
        setBackendProviders(res.data)
        setBackendError(null)
      } else {
        setBackendProviders(null)
        setBackendError(res.error)
      }
    })
  }, [])

  const lock = useCallback(() => {
    clearSessionToken()
    setToken(null)
    setConfig(null)
    setAudit(null)
    setBackendProviders(null)
    setFreshLiveToken(null)
    setNotice([])
    setSaveError(null)
    setAuditError(null)
    setBackendError(null)
    // qa L1 安全防呆：Bedrock 二次確認 dialog 開著時被踢回閘門，若不重置，
    // 重新解鎖後 dialog 會直接呈現在「確認開啟」狀態——等於一鍵開真
    // Bedrock，繞過原本要求的二次確認。
    setConfirmBedrockOn(false)
  }, [])

  // 進頁時若 sessionStorage 有殘留 token → 自動驗證（GET config）；401 就
  // 清掉回到閘門。首次進頁（無 token）直接顯示閘門。
  const verifiedRef = useRef(false)
  useEffect(() => {
    if (!token || verifiedRef.current) return
    verifiedRef.current = true
    const controller = new AbortController()
    setChecking(true)
    getAdminConfig(token, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setChecking(false)
      if (res.ok) {
        applyConfig(res.data)
        refreshAudit(token)
        refreshBackendProviders(token)
      } else {
        lock()
        setGateError(res.error)
      }
    })
    return () => controller.abort()
  }, [token, applyConfig, refreshAudit, refreshBackendProviders, lock])

  async function unlock(e: React.FormEvent) {
    e.preventDefault()
    const candidate = tokenInput.trim()
    if (!candidate) {
      setGateError({ code: 'bad_request', message: t('adminEnterTokenPrompt') })
      return
    }
    setChecking(true)
    setGateError(null)
    const res = await getAdminConfig(candidate)
    setChecking(false)
    if (res.ok) {
      verifiedRef.current = true
      setToken(candidate)
      saveSessionToken(candidate)
      setTokenInput('')
      applyConfig(res.data)
      refreshAudit(candidate)
      refreshBackendProviders(candidate)
    } else {
      setGateError(res.error)
    }
  }

  /** PUT 共用：成功 → 套用新 config + warnings + 重抓審計；409 → 依契約
   * 重新 GET 最新設定並提示「已被他人變更，已重載」；401 → 回到閘門。 */
  /** `extraFailureHint`：qa L3——像「輪替 live token」這種寫入結果不確定
   * 的操作（逾時/網路錯誤時無法得知伺服器端是否真的已寫入新值），失敗
   * 訊息要額外提醒使用者「勿假設舊 token 仍有效」，不能讓人誤以為失敗＝
   * 沒改動、繼續用舊 token。只附加在「一般失敗」分支（網路/逾時/驗證等
   * 非 409/401 情況）——409/401 分支語意已明確（重載/回閘門），不需要
   * 這個額外提醒。 */
  async function doPut(
    changes: AdminConfigChanges,
    extraFailureHint?: string,
  ): Promise<AdminConfigData | null> {
    if (!token || !config) return null
    setSaving(true)
    setSaveError(null)
    setNotice([])
    const res = await putAdminConfig(token, changes, config.version ?? 0)
    if (res.ok) {
      setSaving(false)
        applyConfig(res.data)
        setNotice(res.data.warnings ?? [])
        refreshAudit(token)
        refreshBackendProviders(token)
      return res.data
    }
    if (res.error.code === 'version_conflict') {
      // 計劃 §4-3：重新 GET 拿最新 version，明確提示使用者設定已重載
      const latest = await getAdminConfig(token)
      setSaving(false)
      if (latest.ok) {
        applyConfig(latest.data)
        setNotice([t('adminConfigReloadedNotice')])
      } else if (latest.error.code === 'unauthorized') {
        // qa L2：409 重讀期間 token 可能已失效（被輪替/管理面關閉），
        // 這時不能只顯示 saveError 停在解鎖畫面——與 PUT 401 分支一致，
        // 一律 lock() 回閘門，不留一個「token 其實已失效但畫面還開著」
        // 的假解鎖狀態。
        lock()
        setGateError(latest.error)
      } else {
        setSaveError(latest.error)
      }
      return null
    }
    setSaving(false)
    if (res.error.code === 'unauthorized') {
      lock()
      setGateError(res.error)
      return null
    }
    setSaveError(
      extraFailureHint
        ? { code: res.error.code, message: `${res.error.message}（${extraFailureHint}）` }
        : res.error,
    )
    return null
  }

  async function saveCap() {
    const v = validateCapInput(capInput)
    if (!v.ok) {
      setSaveError({ code: 'invalid_cap', message: v.message })
      return
    }
    await doPut({ daily_cap_usd: v.value })
  }

  async function rotateLiveToken() {
    const plaintext = generateLiveToken()
    const updated = await doPut(
      { live_token: plaintext },
      t('adminRotateFailureHint'),
    )
    if (updated) {
      setFreshLiveToken(plaintext)
      setCopied(false)
    }
  }

  async function clearLiveToken() {
    const updated = await doPut({ live_token: null })
    if (updated) setFreshLiveToken(null)
  }

  async function copyFreshToken() {
    if (!freshLiveToken) return
    try {
      await navigator.clipboard.writeText(freshLiveToken)
      setCopied(true)
    } catch {
      setCopied(false) // clipboard 權限被拒就維持手動選取複製
    }
  }

  async function changeBackendProvider(key: BackendProviderKey, provider: BackendProvider) {
    if (!token) return
    setBackendSaving(true)
    setBackendError(null)
    const res = await setAdminBackendProvider(token, key, provider)
    setBackendSaving(false)
    if (res.ok) {
      setBackendProviders(res.data)
    } else if (res.error.code === 'unauthorized') {
      lock()
      setGateError(res.error)
    } else {
      setBackendError(res.error)
    }
  }

  async function changeAllBackendProviders(provider: BackendProvider) {
    if (!token) return
    setBackendSaving(true)
    setBackendError(null)
    const res = await setAllAdminBackendProviders(token, provider)
    setBackendSaving(false)
    if (res.ok) {
      setBackendProviders(res.data)
    } else if (res.error.code === 'unauthorized') {
      lock()
      setGateError(res.error)
    } else {
      setBackendError(res.error)
    }
  }

  // ── 閘門畫面 ────────────────────────────────────────────────────────
  if (!token || !config) {
    return (
      <main className="mx-auto flex max-w-md flex-col gap-4 px-4 py-10 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
        <h1 className="text-lg font-semibold text-tf-text">{t('adminTitle')}</h1>
        <p className="text-sm text-tf-muted">
          {t('adminGateDescPre')}<code>TRUSTFORGE_ADMIN_TOKEN</code>{t('adminGateDescPost')}
        </p>
        {checking ? (
          <LoadingState label={t('adminVerifying')} />
        ) : (
          <form onSubmit={unlock} className="flex flex-col gap-3">
            <input
              type="password"
              // harper LOW / qa L5：`autoComplete="off"` 對現代瀏覽器的密碼
              // 管理器幾乎無效，反而抑制不了「要不要儲存這個密碼」的提示
              // ——違反本頁「token 不落任何持久儲存」的哲學（讓瀏覽器密碼
              // 管理器存了 admin token，等於繞過我們刻意不用 localStorage
              // 的防線）。`"new-password"` 是瀏覽器公認會抑制儲存提示的值。
              autoComplete="new-password"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder={t('adminTokenPlaceholder')}
              aria-label={t('adminTokenAriaLabel')}
              className="rounded-md border border-tf-border bg-tf-bg px-3 py-2 text-sm text-tf-text"
            />
            <button type="submit" className={BTN_PRIMARY} disabled={checking}>
              {t('adminEnterButton')}
            </button>
          </form>
        )}
        {gateError && <ErrorState code={gateError.code} message={gateError.message} />}
      </main>
    )
  }

  // ── 已解鎖畫面 ──────────────────────────────────────────────────────
  const cap = config.daily_cap_usd
  const bedrock = config.bedrock_enabled
  const multiAngleNarration = config.multi_angle_narration_enabled
  const hermesAutonomy = config.hermes_autonomy_enabled
  const liveToken = config.live_token
  const capCheck = validateCapInput(capInput)

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-tf-text">{t('adminTitle')}</h1>
        <div className="flex items-center gap-3 text-xs text-tf-muted">
          <span className="tf-num">
            {t('adminVersionPrefix')}{config.version ?? 0}
            {config.updated_at ? `・${config.updated_at}` : ''}
            {config.updated_by ? `・${config.updated_by}` : ''}
          </span>
          <button type="button" onClick={lock} className={BTN_PLAIN}>
            {t('adminLockButton')}
          </button>
        </div>
      </div>

      {config.version_corrupt && (
        <ErrorState
          code="version_corrupt"
          message={t('adminVersionCorruptMsg')}
        />
      )}

      {notice.map((msg, i) => (
        <div
          key={i}
          role="status"
          className="rounded-lg border border-tf-warn/60 bg-tf-card p-3 text-sm text-tf-warn"
        >
          {msg}
        </div>
      ))}
      {saveError && <ErrorState code={saveError.code} message={saveError.message} />}

      {/* §4-2 每日 cap */}
      <SectionCard title={t('adminCapSectionTitle')}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            {t('adminEffectiveLabel')}<strong className="tf-num text-tf-text">${cap.effective}</strong>{t('adminPerDay')}
          </span>
          <SourceBadge source={cap.source} />
        </div>
        <p className="mb-3 text-xs text-tf-muted">
          {t('adminCapDetailPrefix')}{cap.config !== null ? `$${cap.config}` : t('adminNotSet')}{t('adminCapDetailEnvLabel')}
          {cap.env !== null ? `「${cap.env}」` : t('adminNotSet')}{t('adminCapDetailDefaultLabel')}{cap.default}{t('adminCapDetailKillSwitch')}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={ADMIN_CAP_MIN_USD}
            max={ADMIN_CAP_MAX_USD}
            step={0.1}
            value={capInput}
            onChange={(e) => setCapInput(e.target.value)}
            aria-label={t('adminCapInputAriaLabel')}
            className="tf-num w-32 rounded-md border border-tf-border bg-tf-bg px-3 py-1.5 text-sm text-tf-text"
          />
          <button
            type="button"
            onClick={saveCap}
            disabled={saving || !capCheck.ok || config.version_corrupt}
            className={BTN_PRIMARY}
          >
            {t('adminSaveCapButton')}
          </button>
        </div>
        {!capCheck.ok && capInput.trim() !== '' && (
          <p className="mt-2 text-xs text-tf-bad" role="alert">
            {capCheck.message}
          </p>
        )}
        <p className="mt-2 text-xs text-tf-muted">
          {t('adminCapRangeHintPre')}{ADMIN_CAP_MIN_USD}–{ADMIN_CAP_MAX_USD}{t('adminCapRangeHintPost')}
        </p>
      </SectionCard>

      {/* §4-2 Bedrock 開關（二態明確 + 開啟二次確認） */}
      <SectionCard title={t('adminBedrockSectionTitle')}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            {t('adminEffectiveLabel')}
            <strong className={bedrock.effective ? 'text-tf-warn' : 'text-tf-text'}>
              {bedrock.effective ? t('adminBedrockOnLabel') : t('adminBedrockOffLabel')}
            </strong>
          </span>
          <SourceBadge source={bedrock.source} />
          {!bedrock.bedrock_model_id_set && (
            <span className="text-xs text-tf-muted">
              {t('adminBedrockModelNotSetHint')}
            </span>
          )}
        </div>
        {confirmBedrockOn ? (
          <div
            role="alertdialog"
            aria-label={t('adminBedrockConfirmDialogAriaLabel')}
            className="rounded-lg border border-tf-warn/60 bg-tf-bg p-3"
          >
            <p className="text-sm font-semibold text-tf-warn">{t('adminBedrockConfirmTitle')}</p>
            <p className="mt-1 text-sm text-tf-text2">
              {t('adminBedrockConfirmBodyPre')}<strong className="tf-num">${cap.effective}</strong>{t('adminBedrockConfirmBodyPost')}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                disabled={saving}
                onClick={async () => {
                  setConfirmBedrockOn(false)
                  await doPut({ bedrock_enabled: true })
                }}
                className={BTN_PRIMARY}
              >
                {t('adminBedrockConfirmYes')}
              </button>
              <button
                type="button"
                onClick={() => setConfirmBedrockOn(false)}
                className={BTN_PLAIN}
              >
                {t('adminCancel')}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            {bedrock.effective ? (
              <button
                type="button"
                disabled={saving || config.version_corrupt}
                onClick={() => doPut({ bedrock_enabled: false })}
                className={BTN_PRIMARY}
              >
                {t('adminBedrockSwitchOff')}
              </button>
            ) : (
              <button
                type="button"
                disabled={saving || config.version_corrupt}
                onClick={() => setConfirmBedrockOn(true)}
                className={BTN_PLAIN}
              >
                {t('adminBedrockSwitchOnPrompt')}
              </button>
            )}
          </div>
        )}
      </SectionCard>

      <SectionCard title={t('adminMultiAngleNarrationSectionTitle')}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            {t('adminEffectiveLabel')}
            <strong className={multiAngleNarration.effective ? 'text-tf-warn' : 'text-tf-text'}>
              {multiAngleNarration.effective
                ? t('adminMultiAngleNarrationOnLabel')
                : t('adminMultiAngleNarrationOffLabel')}
            </strong>
          </span>
          <SourceBadge source={multiAngleNarration.source} />
        </div>
        <p className="mb-3 text-xs text-tf-muted">
          {t('adminMultiAngleNarrationDesc')}
          {multiAngleNarration.env !== null
            ? ` ${t('adminMultiAngleNarrationEnvPrefix')}「${multiAngleNarration.env}」`
            : ''}
        </p>
        {multiAngleNarration.config !== false ? (
          <button
            type="button"
            disabled={saving || config.version_corrupt}
            onClick={() => doPut({ multi_angle_narration_enabled: false })}
            className={BTN_PRIMARY}
          >
            {t('adminMultiAngleNarrationSwitchOff')}
          </button>
        ) : (
          <button
            type="button"
            disabled={saving || config.version_corrupt}
            onClick={() => doPut({ multi_angle_narration_enabled: true })}
            className={BTN_PLAIN}
          >
            {t('adminMultiAngleNarrationSwitchOn')}
          </button>
        )}
      </SectionCard>

      <SectionCard title={t('adminHermesAutonomySectionTitle')}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            {t('adminEffectiveLabel')}
            <strong className={hermesAutonomy.effective ? 'text-tf-warn' : 'text-tf-text'}>
              {hermesAutonomy.effective ? t('adminHermesAutonomyOnLabel') : t('adminHermesAutonomyOffLabel')}
            </strong>
          </span>
          <SourceBadge source={hermesAutonomy.source} />
        </div>
        <p className="mb-3 text-xs text-tf-muted">
          {t('adminHermesAutonomyDescPre')}{hermesAutonomy.env !== null ? `「${hermesAutonomy.env}」` : t('adminNotSet')}{t('adminHermesAutonomyDescPost')}
        </p>
        <div className="flex gap-2">
          {hermesAutonomy.effective ? (
            <button
              type="button"
              disabled={saving || config.version_corrupt}
              onClick={() => doPut({ hermes_autonomy_enabled: false })}
              className={BTN_PRIMARY}
            >
              {t('adminHermesAutonomyOff')}
            </button>
          ) : (
            <button
              type="button"
              disabled={saving || config.version_corrupt}
              onClick={() => doPut({ hermes_autonomy_enabled: true })}
              className={BTN_PLAIN}
            >
              {t('adminHermesAutonomyOnPrompt')}
            </button>
          )}
        </div>
      </SectionCard>

      <SectionCard title={t('adminBackendSectionTitle')}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm text-tf-text2">
          <p>
            {t('adminBackendDescPre')}<strong className="text-tf-text">{t('adminBackendDescBuiltin')}</strong>{t('adminBackendDescPost')}
          </p>
          {backendProviders && (
            <span className="text-xs text-tf-muted">
              {backendProviders.hot_config ? t('adminHotConfig') : t('adminRestartRequiredNeed')} ·
              {backendProviders.restart_required ? ` ${t('adminRestartRequired')}` : ` ${t('adminNoRestart')}`}
            </span>
          )}
        </div>
        {backendError && <ErrorState code={backendError.code} message={backendError.message} />}
        {backendProviders === null && backendError === null ? (
          <LoadingState label={t('adminBackendLoadingLabel')} />
        ) : backendProviders ? (
          <div className="grid gap-3">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={backendSaving}
                onClick={() => changeAllBackendProviders('agentcore')}
                className={BTN_PRIMARY}
              >
                {t('adminSwitchAllAgentcore')}
              </button>
              <button
                type="button"
                disabled={backendSaving}
                onClick={() => changeAllBackendProviders('builtin')}
                className={BTN_PLAIN}
              >
                {t('adminSwitchAllBuiltin')}
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {backendProviders.provider_keys.map((key) => (
                <label
                  key={key}
                  className="flex items-center justify-between gap-3 rounded-md border border-tf-border bg-tf-bg p-3 text-sm"
                >
                  <span>
                    <span className="font-semibold text-tf-text">{BACKEND_PROVIDER_LABELS[key]}</span>
                    <code className="ml-2 text-xs text-tf-muted">backend.{key}</code>
                  </span>
                  <select
                    value={backendProviders.providers[key]}
                    disabled={backendSaving}
                    onChange={(e) => changeBackendProvider(key, e.target.value as BackendProvider)}
                    className="rounded border border-tf-border bg-tf-card px-2 py-1 text-sm text-tf-text"
                    aria-label={`${BACKEND_PROVIDER_LABELS[key]}${t('adminBackendSelectAriaLabelSuffix')}`}
                  >
                    <option value="builtin">{t('adminBuiltinOption')}</option>
                    <option value="agentcore">{t('adminAgentcoreOption')}</option>
                  </select>
                </label>
              ))}
            </div>
          </div>
        ) : null}
      </SectionCard>

      {/* §4-2 live token 管理 */}
      <SectionCard title={t('adminLiveTokenSectionTitle')}>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            {t('adminLiveTokenStatusLabel')}
            <strong className="text-tf-text">
              {liveToken.effective_configured
                ? `${t('adminLiveTokenConfiguredPrefix')}${liveToken.source === 'config' && liveToken.config_last4 ? `${t('adminLiveTokenLast4Prefix')}${liveToken.config_last4}${t('adminLiveTokenLast4Suffix')}` : ''}`
                : t('adminLiveTokenNotConfigured')}
            </strong>
          </span>
          <SourceBadge source={liveToken.source} />
          {liveToken.env_configured && liveToken.source === 'config' && (
            <span className="text-xs text-tf-muted">
              {t('adminLiveTokenEnvHint')}
            </span>
          )}
        </div>

        {freshLiveToken && (
          <div className="mb-3 rounded-lg border border-tf-warn/60 bg-tf-bg p-3" role="status">
            <p className="text-sm font-semibold text-tf-warn">
              {t('adminFreshTokenNoticeTitle')}
            </p>
            <code className="tf-num mt-2 block break-all rounded bg-tf-card p-2 text-xs text-tf-text">
              {freshLiveToken}
            </code>
            {/* harper LOW：剪貼簿是明文常駐面（其他 app/擴充功能可能讀取），
                提示使用者盡快貼入目的地並清空，降低停留時間。 */}
            <p className="mt-1 text-xs text-tf-muted">
              {t('adminFreshTokenClipboardHint')}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" onClick={copyFreshToken} className={BTN_PRIMARY}>
                {copied ? t('adminCopied') : t('adminCopy')}
              </button>
              <button
                type="button"
                onClick={() => setFreshLiveToken(null)}
                className={BTN_PLAIN}
              >
                {t('adminSavedClose')}
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={saving || config.version_corrupt}
            onClick={rotateLiveToken}
            className={BTN_PRIMARY}
          >
            {t('adminRotateTokenButton')}
          </button>
          <button
            type="button"
            disabled={saving || config.version_corrupt || !liveToken.config_configured}
            onClick={clearLiveToken}
            className={BTN_PLAIN}
          >
            {t('adminClearTokenButton')}
          </button>
        </div>
        <p className="mt-2 text-xs text-tf-muted">
          {t('adminTokenGenHint')}
        </p>
      </SectionCard>

      {/* §4-4 審計檢視（唯讀） */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-tf-text">{t('adminAuditSectionTitle')}</h2>
        {audit === null && auditError === null && <LoadingState label={t('adminAuditLoadingLabel')} />}
        {auditError && <ErrorState code={auditError.code} message={auditError.message} />}
        {audit !== null && <AuditTable records={audit} />}
      </section>
    </main>
  )
}
