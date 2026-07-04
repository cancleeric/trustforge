// 純顯示層格式化 helper——UX Round1 稽核 #6：機器可讀的秒數/epoch 不該
// 直接丟給終端使用者（見 `docs/UXUI-ROUND-01.md`）。這裡只做格式轉換，
// 不含任何資料驗證邏輯（那是 `validators.ts` 的職責），輸入皆假設已通過
// runtime guard。

/** `uptime_seconds`（`/api/status`、`/api/health`）→「N 天 N 小時」一類人性
 * 化時長，不足 1 分鐘顯示「不到 1 分鐘」。 */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const totalMinutes = Math.floor(seconds / 60)
  if (totalMinutes < 1) return '不到 1 分鐘'
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60
  const parts: string[] = []
  if (days > 0) parts.push(`${days} 天`)
  if (hours > 0) parts.push(`${hours} 小時`)
  if (days === 0 && minutes > 0) parts.push(`${minutes} 分鐘`)
  return parts.join(' ') || '不到 1 分鐘'
}

/** `age_seconds`（`/api/status` 鮮度矩陣）→「剛剛／N 分鐘前／N 小時前／
 * N 天前」相對時間，`null`（該格 `missing`，沒有時間可言）回 `'—'`。 */
export function formatAge(ageSeconds: number | null): string {
  if (ageSeconds === null || !Number.isFinite(ageSeconds)) return '—'
  if (ageSeconds < 0) return '剛剛'
  const minutes = Math.floor(ageSeconds / 60)
  if (minutes < 1) return '剛剛'
  if (minutes < 60) return `${minutes} 分鐘前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小時前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

/** epoch 秒（`fetched_at`）→ 本地「MM/DD HH:mm」，`null` 回 `'—'`。 */
export function formatEpoch(epochSeconds: number | null): string {
  if (epochSeconds === null || !Number.isFinite(epochSeconds)) return '—'
  const d = new Date(epochSeconds * 1000)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}/${dd} ${hh}:${mi}`
}

/** 成本金額格式化：固定 4 位小數（多數 run 成本落在 $0.00xx 量級，2 位
 * 小數會全部顯示成 $0.00 失去區分度），千分位不需要（金額量級小）。 */
export function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`
}
