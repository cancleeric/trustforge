# Demo 證據封存 Checklist（#204）

> 目的：決賽 Live Demo（桌機 + 手機）證據封存，確保每個關鍵畫面都有截圖 + 錄影。
> **此檔只列清單，不執行**。執行由真人擇時一次收齊。
>
> Demo URL：`http://3.106.220.68/`

---

## 截圖工具建議

| 工具 | 用途 |
|------|------|
| **Playwright `screenshot`** | 自動化兩視口一鍵收齊，可重複執行 |
| **Chrome DevTools Device Mode** | 手動快速切換 375×812（iPhone SE）與 1440×900（桌機） |
| **macOS `screencapture`** | CLI 備援（`screencapture -T2 file.png`） |

推薦做法：寫一個 Playwright script 統一收（見最末節範例），或手動用 DevTools 逐頁切兩視口截。

## 錄影工具建議

| 工具 | 用途 |
|------|------|
| **OBS Studio**（開源）| 全螢幕或視窗錄製，可疊加品牌浮水印 |
| **QuickTime Player**（macOS 內建）| `檔案 > 新增螢幕錄製`，簡單快速 |
| **Playwright `video: 'on'`** | 自動錄製操作過程（無聲音），適合備援 |

推薦做法：OBS 錄 1920×1080 桌機操作全程，手機用 Xcode Simulator 錄（或 Playwright 手機視口錄影）。

---

## 需要封存的畫面清單

> 每項截圖產兩張：**Desktop (1440×900)** + **Mobile (375×812)**

### A. 主頁（Hero / Market Snapshot）

- [ ] **A1.** 主頁全貌（5 幣信任卡 + 來源健康度 + Hero CTA「新增分析」）
  - URL：`http://3.106.220.68/`
  - 檢查項：至少 BTC/ETH 有可信分數、來源新鮮/過期/缺席數字有值
- [ ] **A2.** Hover 任一幣卡看 hover 狀態
- [ ] **A3.** 頁尾（Footer / 團隊資訊）

### B. 發起分析（選幣種 + 提問）

- [ ] **B1.** 從主頁點「新增分析 →」跳轉到 `/analyze?coin=BTC&type=multi_source`
  - 檢查項：BTC 已預選、預設提問文字已帶入
- [ ] **B2.** 下拉 Coin Selector 展開幣種清單（展示可用幣種池）
- [ ] **B3.** 切換探索模式（multi_source / single_source / beginner 等）
- [ ] **B4.** 點「開始分析」→ 進度條/Stage bar 顯示中

### C. 分析結果報告（Analysis Report View）

- [ ] **C1.** 完整報告頁（含 HermesExecutionPanel + TrustBreakdown + EvidenceTrailPanel + CrossSourceSignalPanel）
  - URL 格式：`/analyze?coin=BTC&type=multi_source&q=...`
  - 檢查項：信任分數有值、各組件皆渲染
- [ ] **C2.** HermesExecutionPanel — 五大執行節點（來源蒐集→主張抽取→信任推理→證據組裝→報告交付）
  - 檢查項：各節點有 events 數、耗時；**完成節點顯示綠色邊框**
- [ ] **C3.** HermesExecutionPanel — 來源執行明細表格（每個來源的狀態、文件數、耗時）
- [ ] **C4.** EvidenceTrailPanel（信任溯源概覽卡 #171）— 五張 stat card：
  - 證據總筆數、獨立來源數、操縱紅旗筆數、中性相似提示筆數、跨源訊號
- [ ] **C5.** TrustBreakdown 元件 — 聲譽/佐證/即時性/抗操縱四大信任維度
- [ ] **C6.** CrossSourceSignalPanel（若存在跨源背離）
- [ ] **C7.** HermesExecutionPanel 的三個下載按鈕（**報告** / **Evidence** / **Log**）— 各按一次展示有產出檔案

### D. Breakdown Drawer（StageDrilldown，#542 相關）

- [ ] **D1.** 點「查看完整拆解與推理 →」打開 StageDrilldown/Drawer
  - 檢查項：信任雷達圖（trust_radar）、各 component 權重與分數、推理步驟鏈
- [ ] **D2.** Evidence 表格（`<EvidenceTable>`）展開 — 每筆 evidence 的來源、立場、內容摘要
- [ ] **D3.** ⬇ JSON 下載按鈕（產出 `{runId}-breakdown.json`，含 evidence + trust_radar + execution_log）
- [ ] **D4.** Drawer 關閉 → 回到報告頁

### E. 執行歷程下載（Execution Log）

- [ ] **E1.** HermesExecutionPanel → 點「Log」按鈕下載完整 execution log JSON
  - 檢查項：包含 `execution` manifest + 所有 event
- [ ] **E2.** HermesExecutionPanel → 點「報告」按鈕下載 Markdown 報告
- [ ] **E3.** HermesExecutionPanel → 點「Evidence」按鈕下載 evidence JSON

### F. Hermes Dashboard（若決賽展示流程需要）

- [ ] **F1.** HermesDashboard 主頁面 — CurrencyGalaxy 星系圖 + 幣種互動
- [ ] **F2.** HermesRightRail — 信任分數圓環圖 + 信任 Breakdown + Divergence 指示器

### G. 行動版專屬檢查項（Mobile 375×812）

- [ ] **G1.** 主頁卡片是否正確堆疊（非橫排爆版）
- [ ] **G2.** QueryConsole（幣種/模式/提問）在窄螢幕是否可用
- [ ] **G3.** AnalysisReportView 各組件直排是否正常（無橫向溢出）
- [ ] **G4.** 執行節點 5 列是否直排（md:grid-cols-5 → 窄螢幕應為 1 欄）
- [ ] **G5.** Breakdown Drawer 在窄螢幕是否可用（觸控友善、內容不溢出）

---

## 檔案命名規範

```
evidence/
├── screenshots/
│   ├── desktop/
│   │   ├── A1-home-hero-desktop.png
│   │   ├── B1-coin-select-desktop.png
│   │   ├── C1-report-full-desktop.png
│   │   ├── C2-execution-nodes-desktop.png
│   │   ├── C4-evidence-panel-desktop.png
│   │   ├── D1-breakdown-drawer-desktop.png
│   │   └── E1-log-download-desktop.png
│   └── mobile/
│       ├── A1-home-hero-mobile.png
│       ├── C1-report-full-mobile.png
│       ├── D1-breakdown-drawer-mobile.png
│       └── G4-execution-nodes-stacked-mobile.png
├── video/
│   ├── desktop-demo-walkthrough.mp4      # 桌機全程操作錄影
│   └── mobile-demo-walkthrough.mp4       # 手機全程操作錄影
└── downloads/                            # 從 UI 實際下載的產物樣本
    ├── {runId}-breakdown.json
    ├── {runId}-report.md
    ├── {runId}-evidence.json
    └── execution-log.json
```

---

## 建議 Playwright 批次截圖 Script（參考）

```typescript
// evidence/collect-screenshots.ts
import { chromium } from 'playwright'

const BASE = 'http://3.106.220.68'
const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  mobile:  { width: 375, height: 812 },
}
const PAGES = [
  { id: 'A1', path: '/' },
  { id: 'B1', path: '/analyze?coin=BTC&type=multi_source&q=分析BTC近期市場狀況，整合多源資料' },
  // ... 需已存在分析結果的 URL 才能截 C/D/E（否則要先手動跑一次分析）
]

;(async () => {
  const browser = await chromium.launch()
  for (const [label, vp] of Object.entries(VIEWPORTS)) {
    const context = await browser.newContext({ viewport: vp })
    const page = await context.newPage()
    for (const { id, path } of PAGES) {
      await page.goto(BASE + path, { waitUntil: 'networkidle' })
      await page.waitForTimeout(2000) // 等動畫完成
      await page.screenshot({
        path: `evidence/screenshots/${label}/${id}-${path.replace(/\//g, '-')}-${label}.png`,
        fullPage: true,
      })
    }
    await context.close()
  }
  await browser.close()
})()
```

> ⚠️ 分析結果頁需要先手動執行一次分析並記錄 URL（含 `job_id` 或 run 參數），或使用 History page 的既有分析連結。

---

## 決賽當天快速檢查表

| # | 項目 | 狀態 |
|---|------|------|
| 1 | Demo URL 可正常存取 | ☐ |
| 2 | 主頁 5 幣信任分數正常顯示 | ☐ |
| 3 | 可成功發起分析（Job 不卡 queued） | ☐ |
| 4 | 分析報告各組件渲染無破版 | ☐ |
| 5 | Execution nodes 五節點皆有數據 | ☐ |
| 6 | Evidence Panel 五張 stat card 有值 | ☐ |
| 7 | Breakdown Drawer 可開啟、可下載 JSON | ☐ |
| 8 | 報告 / Evidence / Log 三個下載按鈕皆可產出檔案 | ☐ |
| 9 | 手機視口無爆版、無橫向溢出 | ☐ |
| 10 | OBS/錄影工具已就緒、已測試 | ☐ |

---

## 關聯文件

- `SUBMISSION-CHECKLIST.md` — 決賽交付總 checklist
- `OPS-EVIDENCE-CLOUDWATCH.md` — AWS CloudWatch 營運證據
- `OPS-EVIDENCE-NGINX.md` — Nginx 營運證據
- `KIRO-USAGE-EVIDENCE.md` — KIRO 框架使用證據
- `COMPETITION.md` — 競賽規格
- `PROPOSAL.md` — 參賽企劃書
