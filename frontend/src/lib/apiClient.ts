// TrustForge `/api/*` typed client。
//
// 統一信封 `{ok,data,error}`：後端在 4xx/5xx 時 body 仍是合法 JSON 信封
// （見 `web.py` 各 `/api/*` handler 的 `except Exception` 分支一律回
// `{ok:false,error:{code,message}}`），所以這裡優先解析 body，而不是只看
// HTTP status——但 HTTP >= 400 或 `ok:false` 任一為真都視為錯誤狀態，交由
// 呼叫端渲染錯誤 UI（不得只信任其中一個訊號）。
//
// 同源部署（見 PLAN §5 nginx 反代），不需要帶 credentials/CORS 設定；
// dev 模式走 vite.config.ts 的 `/api` proxy。

import type { ApiEnvelope, ApiFailure } from './types'
import { isPlainObject } from './validators'

/** 一般端點（overview/health，讀 cache，應該很快）預設逾時。 */
export const DEFAULT_TIMEOUT_MS = 10_000
/** 分析端點（真分析，可能觸發真連接器）需要較長逾時。 */
export const ANALYZE_TIMEOUT_MS = 30_000

function networkFailure(message: string): ApiFailure {
  return { ok: false, error: { code: 'network_error', message } }
}

function parseFailure(message: string): ApiFailure {
  return { ok: false, error: { code: 'parse_error', message } }
}

/** 逾時觸發的 abort——後端收了請求但沒回應，不能讓呼叫端永遠 loading。
 * 是獨立的失敗結果，交由呼叫端顯示錯誤 UI（不 throw、不無限 loading）。 */
function timeoutFailure(timeoutMs: number): ApiFailure {
  return {
    ok: false,
    error: { code: 'timeout', message: `請求逾時（超過 ${Math.round(timeoutMs / 1000)} 秒無回應）` },
  }
}

/** 呼叫端主動取消（如 React effect cleanup 因參數變更/元件卸載而中止
 * 「已被取代」的請求）——不是後端故障。呼叫端應該以 `signal.aborted`
 * 判斷並靜默捨棄這個結果，不當錯誤 UI 顯示、不覆蓋新狀態。 */
function cancelledFailure(): ApiFailure {
  return { ok: false, error: { code: 'cancelled', message: '請求已取消' } }
}

/** `ok:false` 分支的信封本身也要驗證，不能盲信後端一定附 `error.code`/`message`。
 * `current_version` 是 `/api/admin/config` PUT 409（`version_conflict`）專用
 * 附帶欄位（後端重讀失敗時為 null）——存在但型別畸形一樣整包轉
 * parse_error，不讓管理頁拿到假 version 去做下一次 CAS。 */
function isValidFailureEnvelope(value: Record<string, unknown>): value is {
  ok: false
  error: { code: string; message: string; retry_href?: string; current_version?: number | null }
} {
  const err = value.error
  return (
    isPlainObject(err) &&
    typeof err.code === 'string' &&
    typeof err.message === 'string' &&
    (err.retry_href === undefined || typeof err.retry_href === 'string') &&
    (err.current_version === undefined ||
      err.current_version === null ||
      typeof err.current_version === 'number')
  )
}

export interface ApiFetchOptions {
  /** 呼叫端傳入的外部 AbortSignal（通常來自 React effect cleanup 的
   * AbortController）。effect 卸載/依賴參數變更時 abort，代表這個請求
   * 已被取代——呼叫端應在 `.then()` 前用 `signal.aborted` 判斷並直接
   * 捨棄結果，不需要依賴回傳的 `error.code`。 */
  signal?: AbortSignal
  /** 逾時毫秒數，超過會 abort 並回傳 `error.code === 'timeout'`
   * （見 `timeoutFailure`）。各端點依實際回應時間給不同預設值，見
   * `DEFAULT_TIMEOUT_MS` / `ANALYZE_TIMEOUT_MS` 與 `endpoints.ts`。 */
  timeoutMs?: number
  /** `/api/admin/config` 寫入用；預設 GET。 */
  method?: 'GET' | 'POST' | 'PUT'
  /** 額外 request headers（如 `X-Admin-Token`）——認證 token 一律走
   * header、絕不進 URL/query（query 會落 access log/瀏覽歷史，見後端
   * `web.py` admin 區塊「token 絕不進 URL」原則，前端同一條紀律）。 */
  headers?: Record<string, string>
  /** 有值時 `JSON.stringify` 後作為 request body 送出，並自動帶
   * `Content-Type: application/json`（後端 PUT 415/411 檢查所需）。 */
  jsonBody?: unknown
  /** qa L4：admin 端點（設定快照含 cap/last4/version 等機敏或易失真
   * 資訊）要求 `'no-store'`，不進瀏覽器 heuristic cache/bfcache——後端
   * no-cache response header 另歸 PR-5，這裡是前端側雙邊防禦，任一邊
   * 生效都足以避免拿到「看起來成功但其實是快取的舊管理設定」。一般
   * `/api/*` 讀取端點不受影響（預設沿用 fetch 標準快取行為）。 */
  cache?: RequestCache
}

export async function apiFetch<T>(
  path: string,
  params: Record<string, string | number | undefined> | undefined,
  /** 端點 payload 的 runtime type guard（見 `validators.ts`），data 形狀不符
   * 一律轉成 `parseFailure`，絕不把半成品資料往下游元件塞。 */
  isValidData: (data: unknown) => data is T,
  options: ApiFetchOptions = {},
): Promise<ApiEnvelope<T>> {
  const { signal: externalSignal, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  const qs = new URLSearchParams()
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') qs.set(key, String(value))
    }
  }
  const url = qs.toString() ? `${path}?${qs.toString()}` : path

  // 內部永遠自帶一個 AbortController：逾時到了就 abort，避免後端 stall
  // 造成 promise 永遠 pending、UI 永遠 loading。呼叫端的外部 signal（若
  // 有）會連動到同一個 controller，兩種 abort 來源都能中止底層 fetch，
  // 但結果要分開判斷（逾時 vs 主動取消），不能混為一談。
  const controller = new AbortController()
  let timedOut = false
  const onExternalAbort = () => controller.abort()
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort()
    else externalSignal.addEventListener('abort', onExternalAbort)
  }
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const requestHeaders: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers ?? {}),
  }
  const init: RequestInit = {
    method: options.method ?? 'GET',
    headers: requestHeaders,
    signal: controller.signal,
    ...(options.cache ? { cache: options.cache } : {}),
  }
  if (options.jsonBody !== undefined) {
    requestHeaders['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.jsonBody)
  }

  let res: Response | undefined
  let body: unknown
  try {
    res = await fetch(url, init)
    body = await res.json()
  } catch (err) {
    if (timedOut) return timeoutFailure(timeoutMs)
    if (externalSignal?.aborted) return cancelledFailure()
    if (res) {
      // fetch 拿到回應了，是 res.json() 解析失敗（非合法 JSON body）。
      return parseFailure(`伺服器回應非 JSON（HTTP ${res.status}）`)
    }
    return networkFailure(err instanceof Error ? err.message : '網路連線失敗')
  } finally {
    clearTimeout(timer)
    if (externalSignal) externalSignal.removeEventListener('abort', onExternalAbort)
  }

  if (!isPlainObject(body)) {
    return parseFailure('伺服器回應格式不符預期（非物件）')
  }

  // `ok` 必須嚴格是 boolean——`{ok:"false"}` 這類字串誤判過去會被
  // `!!body.ok` 一類寫法吃進「成功」分支，是造成白屏的根因之一。
  if (typeof body.ok !== 'boolean') {
    return parseFailure('伺服器回應缺少合法的 ok 欄位')
  }

  if (body.ok === false) {
    if (!isValidFailureEnvelope(body)) {
      return parseFailure('伺服器錯誤回應缺少合法的 error.code/message')
    }
    return { ok: false, error: body.error }
  }

  // body.ok === true：data 必須存在，且通過對應端點的 payload 型別檢查，
  // 否則視同解析失敗，絕不把半成品資料往下游元件塞（會讀 undefined 白屏）。
  if (!isValidData(body.data)) {
    return parseFailure('伺服器成功回應的資料格式不符預期')
  }

  // HTTP >= 400 但 body 宣稱 ok:true 且形狀合法——依然視為不可信，一律
  // 當錯誤處理，不把資料往下游渲染（雙訊號都要檢查，不能只信任一個）。
  if (res.status >= 400) {
    return {
      ok: false,
      error: { code: 'http_error', message: `伺服器回應異常（HTTP ${res.status}）` },
    }
  }

  return { ok: true, data: body.data }
}
