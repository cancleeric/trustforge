// 管理控制台（/admin）純邏輯層：admin token 的分頁級暫存、live token 產生、
// cap 前端驗證、審計顯示格式化。UI 元件（AdminPage.tsx）只做渲染，可測邏輯
// 集中在這裡（本 repo vitest 跑 node 環境、無 DOM testing library，元件層
// 不直接測，見既有 lib/*.test.ts 慣例）。
//
// ⛔ token 儲存策略（計劃 §4-1，harper 掃描重點）：
//   - 主儲存是 React state（記憶體）——XSS 以外無洩露面，分頁關閉即消失。
//   - 輔以 sessionStorage（僅為「同分頁 reload 不用重貼」的 UX），分頁關閉
//     即由瀏覽器清除，不跨分頁共享。
//   - **絕不使用 localStorage**：localStorage 無期限持久化，一次 XSS 就能
//     竊走之後每次造訪都有效的管理 token（持久竊取面）。本檔案外的 src/
//     出現 `localStorage` 字樣會被 `adminConsole.test.ts` 的靜態掃描測試
//     直接打紅。
//   - token 絕不進 URL/query（同 `endpoints.ts` header 紀律）。

const SESSION_KEY = 'tf-admin-token'

/** sessionStorage 可能整個不可用（Safari 隱私模式舊行為、嵌入式 webview、
 * node 測試環境無 DOM）——任何 storage 例外都靜默降級成「純記憶體模式」，
 * 管理頁功能照常，只是 reload 後要重貼 token，絕不讓儲存層例外炸掉頁面。 */
function defaultStorage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

export function loadSessionToken(storage: Storage | null = defaultStorage()): string | null {
  if (!storage) return null
  try {
    const value = storage.getItem(SESSION_KEY)
    return value === null || value === '' ? null : value
  } catch {
    return null
  }
}

export function saveSessionToken(
  token: string,
  storage: Storage | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    storage.setItem(SESSION_KEY, token)
  } catch {
    // 寫不進去就純記憶體模式（見上），不往外拋
  }
}

export function clearSessionToken(storage: Storage | null = defaultStorage()): void {
  if (!storage) return
  try {
    storage.removeItem(SESSION_KEY)
  } catch {
    // 同上，靜默降級
  }
}

/** 產生新 live token：CSPRNG（`crypto.getRandomValues`）32 bytes → 64 字元
 * 小寫 hex。滿足後端 PUT 驗證（24–512 字元、可見 ASCII 0x21–0x7E）且與
 * 管理 token 相同的機率可忽略。⚠️ 明文只存在於呼叫端本次 state（一次性
 * 顯示），本函式不落任何儲存。 */
export function generateLiveToken(cryptoObj: Crypto = globalThis.crypto): string {
  const bytes = new Uint8Array(32)
  cryptoObj.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// 與後端 `web.py::_ADMIN_CAP_MIN_USD`/`_ADMIN_CAP_MAX_USD` 對齊。前端驗證
// 只是即時 UX（擋掉明顯錯誤輸入、不浪費一次 PUT），最終以 server 回應為準
// （後端另擋 NaN/Infinity/巨大整數等，見 `_validate_admin_put_payload`）。
export const ADMIN_CAP_MIN_USD = 0.1
export const ADMIN_CAP_MAX_USD = 50

export type CapValidation = { ok: true; value: number } | { ok: false; message: string }

export function validateCapInput(raw: string): CapValidation {
  const trimmed = raw.trim()
  if (trimmed === '') return { ok: false, message: '請輸入每日花費上限（USD）' }
  const value = Number(trimmed)
  if (!Number.isFinite(value)) {
    return { ok: false, message: '必須是有限數字' }
  }
  if (value < ADMIN_CAP_MIN_USD || value > ADMIN_CAP_MAX_USD) {
    return {
      ok: false,
      message: `須介於 ${ADMIN_CAP_MIN_USD} 至 ${ADMIN_CAP_MAX_USD} USD（緊急全關請改用 Bedrock 開關，不接受 0）`,
    }
  }
  return { ok: true, value }
}

/** 審計表格「舊值/新值」顯示格式化。token 類欄位後端已寫入遮罩值
 * （`admin_config.py::_mask_token_change`：`<set>`/`<unset>`/`<cleared>`/
 * `<rotated last4=xxxx>`/`<set last4=xxxx>`…，絕無明文），這裡再轉成
 * 中文字樣（計劃 §4-4：token 類只顯示「已輪替」級別的資訊，不顯示
 * 遮罩內部格式）。其餘欄位（cap 數字、開關 bool、null＝清除）原樣轉字串。 */
export function formatAuditValue(field: string, value: unknown): string {
  if (field === 'live_token') {
    const raw = typeof value === 'string' ? value : ''
    if (raw.startsWith('<cleared')) return '已清除'
    if (raw.startsWith('<rotated')) return '已輪替'
    if (raw.startsWith('<set')) return '已設定'
    if (raw.startsWith('<unset')) return '未設定'
    return '（遮罩）' // 未知遮罩格式也絕不原樣輸出，防未來格式變動外洩資訊
  }
  if (value === null || value === undefined) return '（清除，回落 env/default）'
  if (typeof value === 'boolean') return value ? '開' : '關'
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  return JSON.stringify(value)
}

/** 三層來源徽章的中文標籤（`source` 是寬字串，未知值原樣顯示，不窮舉）。 */
export function sourceLabel(source: string): string {
  switch (source) {
    case 'config':
      return 'config（管理面）'
    case 'env':
      return 'env（環境變數）'
    case 'default':
      return 'default（內建預設）'
    case 'none':
      return '未設定'
    case 'config_read_error':
      return '讀取異常（fail-closed）'
    default:
      return source
  }
}
