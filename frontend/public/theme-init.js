// #20 主題切換 FOUC 防護。codex 複審 HIGH（CSP 相容）：production React CSP
// 是 `script-src 'self'`（無 nonce/hash/unsafe-inline，見
// `src/trustforge/web.py::_CSP_REACT` 與 `deploy/nginx-react-http.conf`），
// inline `<script>` 會被瀏覽器直接擋掉、FOUC 防護在正式環境每次都失效——
// 必須是「同源外部檔案」＋「同步載入」（不能 defer/async/type=module），
// 才能在任何 CSS 套用、React mount 之前就跑完，設好 `<html data-theme>`。
//
// 邏輯必須跟 `src/lib/theme.ts` 的 `resolveInitialTheme` 保持一致
// （localStorage `tf-theme` 有效值 > 系統偏好 prefers-color-scheme > dark），
// 且只「讀」不「寫」——只從系統偏好推導出的值不是使用者的明確選擇，不可在
// 這裡 persist 進 localStorage（同一顆 codex MEDIUM 修過的坑，這裡不能重踩）。
(function () {
  try {
    var stored = localStorage.getItem('tf-theme');
    var theme =
      stored === 'light' || stored === 'dark'
        ? stored
        : window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
          ? 'light'
          : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
  } catch {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
