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
import { getAdminAudit, getAdminConfig, putAdminConfig } from '../lib/endpoints'
import type { AdminAuditRecord, AdminConfigChanges, AdminConfigData } from '../lib/types'
import { ErrorState, LoadingState } from '../components/StatusStates'

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
  if (records.length === 0) {
    return <p className="text-sm text-tf-muted">目前尚無設定變更紀錄。</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full min-w-[560px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">時間（UTC）</th>
            <th className="px-3 py-2 font-medium">操作者</th>
            <th className="px-3 py-2 font-medium">欄位</th>
            <th className="px-3 py-2 font-medium">舊值 → 新值</th>
            <th className="tf-num px-3 py-2 text-right font-medium">版本</th>
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

export default function AdminPage() {
  // token 主存 React state；初始值嘗試從 sessionStorage 撈（同分頁 reload）
  const [token, setToken] = useState<string | null>(() => loadSessionToken())
  const [tokenInput, setTokenInput] = useState('')
  const [gateError, setGateError] = useState<{ code: string; message: string } | null>(null)
  const [checking, setChecking] = useState(false)

  const [config, setConfig] = useState<AdminConfigData | null>(null)
  const [audit, setAudit] = useState<AdminAuditRecord[] | null>(null)
  const [auditError, setAuditError] = useState<{ code: string; message: string } | null>(null)

  const [capInput, setCapInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string[]>([])
  const [saveError, setSaveError] = useState<{ code: string; message: string } | null>(null)
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

  const lock = useCallback(() => {
    clearSessionToken()
    setToken(null)
    setConfig(null)
    setAudit(null)
    setFreshLiveToken(null)
    setNotice([])
    setSaveError(null)
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
      } else {
        lock()
        setGateError(res.error)
      }
    })
    return () => controller.abort()
  }, [token, applyConfig, refreshAudit, lock])

  async function unlock(e: React.FormEvent) {
    e.preventDefault()
    const candidate = tokenInput.trim()
    if (!candidate) {
      setGateError({ code: 'bad_request', message: '請輸入管理 token' })
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
    } else {
      setGateError(res.error)
    }
  }

  /** PUT 共用：成功 → 套用新 config + warnings + 重抓審計；409 → 依契約
   * 重新 GET 最新設定並提示「已被他人變更，已重載」；401 → 回到閘門。 */
  async function doPut(changes: AdminConfigChanges): Promise<AdminConfigData | null> {
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
      return res.data
    }
    if (res.error.code === 'version_conflict') {
      // 計劃 §4-3：重新 GET 拿最新 version，明確提示使用者設定已重載
      const latest = await getAdminConfig(token)
      setSaving(false)
      if (latest.ok) {
        applyConfig(latest.data)
        setNotice(['設定已被他人變更，已重新載入最新設定——請確認後再送出一次'])
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
    setSaveError(res.error)
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
    const updated = await doPut({ live_token: plaintext })
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

  // ── 閘門畫面 ────────────────────────────────────────────────────────
  if (!token || !config) {
    return (
      <main className="mx-auto flex max-w-md flex-col gap-4 px-4 py-10 sm:px-6">
        <h1 className="text-lg font-semibold text-tf-text">管理控制台</h1>
        <p className="text-sm text-tf-muted">
          本頁需要管理 token（<code>TRUSTFORGE_ADMIN_TOKEN</code>，與 live token
          不同權限層級）。token 只保存在本分頁（記憶體 + sessionStorage），關閉
          分頁即清除。
        </p>
        {checking ? (
          <LoadingState label="驗證中…" />
        ) : (
          <form onSubmit={unlock} className="flex flex-col gap-3">
            <input
              type="password"
              autoComplete="off"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="貼上管理 token"
              aria-label="管理 token"
              className="rounded-md border border-tf-border bg-tf-bg px-3 py-2 text-sm text-tf-text"
            />
            <button type="submit" className={BTN_PRIMARY} disabled={checking}>
              進入管理面
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
  const liveToken = config.live_token
  const capCheck = validateCapInput(capInput)

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-tf-text">管理控制台</h1>
        <div className="flex items-center gap-3 text-xs text-tf-muted">
          <span className="tf-num">
            設定版本 v{config.version ?? 0}
            {config.updated_at ? `・${config.updated_at}` : ''}
            {config.updated_by ? `・${config.updated_by}` : ''}
          </span>
          <button type="button" onClick={lock} className={BTN_PLAIN}>
            鎖定並清除 token
          </button>
        </div>
      </div>

      {config.version_corrupt && (
        <ErrorState
          code="version_corrupt"
          message="設定 version 欄位損毀，需人工修復後才能寫入（見 admin_config.py SOP）——以下表單僅供檢視。"
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
      <SectionCard title="每日 Bedrock 花費上限（USD）">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            生效值：<strong className="tf-num text-tf-text">${cap.effective}</strong>/day
          </span>
          <SourceBadge source={cap.source} />
        </div>
        <p className="mb-3 text-xs text-tf-muted">
          三層對照——config：{cap.config !== null ? `$${cap.config}` : '未設定'}／env 原始值：
          {cap.env !== null ? `「${cap.env}」` : '未設定'}／內建預設：${cap.default}。env 設 0
          為緊急全關 kill-switch（最高優先）。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={ADMIN_CAP_MIN_USD}
            max={ADMIN_CAP_MAX_USD}
            step={0.1}
            value={capInput}
            onChange={(e) => setCapInput(e.target.value)}
            aria-label="每日花費上限（USD）"
            className="tf-num w-32 rounded-md border border-tf-border bg-tf-bg px-3 py-1.5 text-sm text-tf-text"
          />
          <button
            type="button"
            onClick={saveCap}
            disabled={saving || !capCheck.ok || config.version_corrupt}
            className={BTN_PRIMARY}
          >
            儲存上限
          </button>
        </div>
        {!capCheck.ok && capInput.trim() !== '' && (
          <p className="mt-2 text-xs text-tf-bad" role="alert">
            {capCheck.message}
          </p>
        )}
        <p className="mt-2 text-xs text-tf-muted">
          有效範圍 {ADMIN_CAP_MIN_USD}–{ADMIN_CAP_MAX_USD}；前端檢查僅為即時提示，一律以
          伺服器回應為準。
        </p>
      </SectionCard>

      {/* §4-2 Bedrock 開關（二態明確 + 開啟二次確認） */}
      <SectionCard title="Bedrock 真呼叫開關">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            目前生效：
            <strong className={bedrock.effective ? 'text-tf-warn' : 'text-tf-text'}>
              {bedrock.effective ? '真 Bedrock（花錢）' : '離線（安全預設）'}
            </strong>
          </span>
          <SourceBadge source={bedrock.source} />
          {!bedrock.bedrock_model_id_set && (
            <span className="text-xs text-tf-muted">
              （BEDROCK_MODEL_ID 未設定——開關開了也不會有真呼叫）
            </span>
          )}
        </div>
        {confirmBedrockOn ? (
          <div
            role="alertdialog"
            aria-label="開啟真 Bedrock 確認"
            className="rounded-lg border border-tf-warn/60 bg-tf-bg p-3"
          >
            <p className="text-sm font-semibold text-tf-warn">確定要切換到「真 Bedrock」？</p>
            <p className="mt-1 text-sm text-tf-text2">
              開啟後公開流量最多燒 <strong className="tf-num">${cap.effective}</strong>/day
              （當前生效 cap）。這會產生真實 AWS 費用。
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
                確認開啟（花錢）
              </button>
              <button
                type="button"
                onClick={() => setConfirmBedrockOn(false)}
                className={BTN_PLAIN}
              >
                取消
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
                切回離線（安全預設）
              </button>
            ) : (
              <button
                type="button"
                disabled={saving || config.version_corrupt}
                onClick={() => setConfirmBedrockOn(true)}
                className={BTN_PLAIN}
              >
                切換到真 Bedrock（花錢）…
              </button>
            )}
          </div>
        )}
      </SectionCard>

      {/* §4-2 live token 管理 */}
      <SectionCard title="Live token（公開分析 live 模式用，權限低於管理 token）">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-tf-text2">
          <span>
            狀態：
            <strong className="text-tf-text">
              {liveToken.effective_configured
                ? `已設定${liveToken.source === 'config' && liveToken.config_last4 ? `（末 4 碼 ${liveToken.config_last4}）` : ''}`
                : '未設定'}
            </strong>
          </span>
          <SourceBadge source={liveToken.source} />
          {liveToken.env_configured && liveToken.source === 'config' && (
            <span className="text-xs text-tf-muted">
              （env 層另有 token，但 config 層優先生效）
            </span>
          )}
        </div>

        {freshLiveToken && (
          <div className="mb-3 rounded-lg border border-tf-warn/60 bg-tf-bg p-3" role="status">
            <p className="text-sm font-semibold text-tf-warn">
              新 live token（僅此一次顯示——離開此畫面後無法再看到，後端只存 hash）
            </p>
            <code className="tf-num mt-2 block break-all rounded bg-tf-card p-2 text-xs text-tf-text">
              {freshLiveToken}
            </code>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" onClick={copyFreshToken} className={BTN_PRIMARY}>
                {copied ? '已複製' : '複製'}
              </button>
              <button
                type="button"
                onClick={() => setFreshLiveToken(null)}
                className={BTN_PLAIN}
              >
                我已保存，關閉
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
            產生新 token 並輪替
          </button>
          <button
            type="button"
            disabled={saving || config.version_corrupt || !liveToken.config_configured}
            onClick={clearLiveToken}
            className={BTN_PLAIN}
          >
            清除 runtime token（回落 env 層）
          </button>
        </div>
        <p className="mt-2 text-xs text-tf-muted">
          token 由瀏覽器 CSPRNG 產生（32 bytes hex）；輪替後舊 token（含 env 層）立即失效。
        </p>
      </SectionCard>

      {/* §4-4 審計檢視（唯讀） */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-tf-text">設定變更審計（近 50 筆）</h2>
        {audit === null && auditError === null && <LoadingState label="審計紀錄載入中…" />}
        {auditError && <ErrorState code={auditError.code} message={auditError.message} />}
        {audit !== null && <AuditTable records={audit} />}
      </section>
    </main>
  )
}
