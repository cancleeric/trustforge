// harper 安全審 must-have：任何要塞進 `<a href>` 的外部 URL（evidence 來源
// 連結、price_provenance 連結等）在渲染前一律先過這裡的 scheme allowlist。
// 只允許 http/https，明確擋 javascript:/data:/vbscript: 等可執行/可注入
// scheme，避免 XSS（React 本身雖會 escape 文字節點，但 `href` 屬性若原封
// 不動塞入 `javascript:...` 仍可被使用者點擊觸發）。
//
// 用法：`safeHref(url)` 回傳合法就回原字串，否則回 `null`——呼叫端遇到
// `null` 一律不渲染 `<a>`，改渲染純文字（不得靜默 fallback 成其他連結，
// 避免掉包成使用者未預期的目的地）。

const ALLOWED_PROTOCOLS = new Set(['http:', 'https:'])

export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  const trimmed = url.trim()
  if (!trimmed) return null

  let parsed: URL
  try {
    // 刻意不帶 base：只接受本身就是合法絕對 URL 的字串，避免相對路徑
    // 被误判、也避免 `//evil.com`（protocol-relative）繞過 scheme 檢查
    // ——`new URL()` 在無 base 時，protocol-relative 字串會直接解析失敗。
    parsed = new URL(trimmed)
  } catch {
    return null
  }

  if (!ALLOWED_PROTOCOLS.has(parsed.protocol)) {
    return null
  }

  return parsed.toString()
}
