# TrustForge Frontend（Phase 2a）

React + Vite + TypeScript + Tailwind CSS 前後端分離前端骨架，消費既有
`/api/*` JSON API（見 `../docs/architecture/PLAN-frontend-backend-split.md`）。**不影響**
既有 `src/` SSR 後端，純靜態 build 產物。

## 開發

```bash
npm install
npm run dev      # http://localhost:5173，/api/* 由 vite.config.ts proxy 轉發
```

預設 proxy 指向 live API（`http://13.211.110.218`）。若本機另起後端，用
`VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev` 覆寫。

## Build

```bash
npm run build     # tsc -b && vite build，輸出至 dist/（純靜態檔）
npm run preview   # 本機預覽 build 產物
```

## 目錄

- `src/lib/` — typed API client（`apiClient.ts`/`endpoints.ts`/`types.ts`）、
  安全 util（`safeHref.ts` scheme allowlist）、來源品牌/tier 對照
  （`sourceBrand.ts`）
- `src/components/` — 可重用 UI 元件（信任 gauge、雷達圖、證據表、跨源
  分歧面板等）
- `src/pages/` — `HomePage`（多幣總覽）、`AnalyzePage`（分析報告）、
  `NotFoundPage`

## 安全

- 全專案禁 `dangerouslySetInnerHTML`
- 所有外部連結（來源連結）一律先過 `safeHref()`（僅 http/https）
- CSP-friendly：無 inline script/eval，build 產物皆為 hash 檔名的 self-host
  資源

## 已知範圍（Phase 2a）

- 已接：首頁多幣總覽、分析頁（multi_source/hypothesis）
- 未接（Phase 2b）：`comparison` 題型（後端回傳 `report_a`/`report_b` 雙報告
  結構，與目前 `AnalyzeData` 型別不同，需另開頁面/型別）、`/status`、
  `/costs`、`/history`（PIT 趨勢圖）
