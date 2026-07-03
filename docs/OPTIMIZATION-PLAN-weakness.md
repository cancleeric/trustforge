# TrustForge 優化計劃 — 弱點綜合（致命度 × 可行性排序）

> 綜合 CEO 派下兩路批判（核心弱點分析 + UI code-grounded 審查）。誠實標註：
> 哪些是黑客松展示能修好的「止血」，哪些是離「真商用可賣」還有結構性距離的
> 「戰略抉擇」。**不改 code**，本文件只列計劃。

---

## Phase 1｜UI 快修（本輪可派 CTO，$0，本輪內止血，防買家/評審打槍）

檔案皆為 `src/trustforge/web.py`（多幣卡另涉 `scripts/fetch_scheduler.py`）。

| # | 問題 | 具體修法 | 改哪裡 | Chrome 驗收 checklist |
|---|------|---------|--------|----------------------|
| 1 | 多幣總覽卡是死 `<div>`（`_render_overview_html`，無 `<a>`/無 pointer/無 onclick）[已在修] | 卡片外包 `<a href="/analyze?coin={coin}&type=multi_source&...">`，CSS 補 `cursor:pointer` + hover 效果 | `scripts/fetch_scheduler.py::_render_overview_html`（產字串處）+ `web.py` CSS `.tf-overview-card` | 開首頁 → 滑鼠移到任一幣卡看到手型游標 → 點擊 → 導到該幣 `/analyze` 結果頁且幣種一致 |
| 2 | 比較分析表單斷：Query Console 只有單一 `<select name="coin">`，選「比較分析」題型後送出 → `_parse_comparison_coins` 找不到第二幣 → `ValueError` 洩露內部參數字串 `coin=BTC,ETH` 給使用者看 | (a) 題型選「比較分析」時，用純 CSS/最小 JS 顯示第二個幣種 `<select name="coin2">`，送出前組成 `coin=A,B`；或 (b) 兩選項互斥前先擋：後端保留現有「文字含兩幣種」偵測作 fallback，前端在比較分析題型下把 textarea placeholder 改成明確引導「請在問題中提及兩個幣種，如 BTC 與 ETH」，避免無提示直接炸 400 | `web.py` `_PAGE` 表單區（~L308-317）+ `_do_comparison`/`_parse_comparison_coins` 錯誤訊息（改成不含裸 querystring 語法的使用者可讀文字） | 首頁選「比較分析」→ 送出未含兩幣的問題 → 應看到清楚中文提示（非 `coin=BTC,ETH` 這種內部語法），且有明確路徑（第二欄或文字引導）能成功送出比較 |
| 3 | 錯誤頁裸紅字（429/400/502 皆 `<p style='color:#c00'>...</p>`），404 只顯示 `<p>404</p>` | 統一錯誤 body 包成品牌化卡片：`.tf-section` 卡 + 標題（依狀態碼："請求過於頻繁"/"輸入有誤"/"服務暫時無法使用"/"找不到頁面"）+ 一個「返回首頁」`<a href="/">` 按鈕，維持既有 `page()` 包裝（header/CSS 不動） | `web.py` L2831-2843（429/400/502/404 四處 `_send`）新增一個共用 `_render_error_card(title, detail)` helper | 手動觸發各狀態碼（如 `/analyze?coin=XXX`→400、`/nope`→404）→ 每頁都看到卡片式排版 + 「返回首頁」按鈕可點回 `/` |
| 4 | 內頁無回首頁：logo 是純 `<span class="tf-logo">`，非連結 | logo 外包 `<a href="/" style="text-decoration:none;color:inherit">` | `web.py` L1822（`logo = ...`，`render_page`/header 共用） | 在 `/status`、`/costs`、任一 `/analyze` 結果頁點 logo → 導回首頁 |
| 5 | loading 脆弱：純靠 `button:active` CSS 偽狀態模擬 loading，滑鼠放開/鍵盤觸發不穩定，且無真正禁用機制 | **已拍板維持 zero-JS**：`inline onsubmit` 需要 JS，會破壞現有 strict CSP（`default-src 'none'`），非本輪範圍——保留 CSS `:active` best-effort loading（有勝於無，不做誤導性承諾）；相關程式碼註解已改誠實聲明「不保證防重複送出」（見下方 codex MEDIUM follow-up） | `web.py` `_PAGE` CSS 註解（`button[type=submit]:active` 區塊） | 送出查詢瞬間按鈕短暫呈現 loading 樣式（放開/導航中仍可再點——**已知限制，非本輪修復目標**） |
| 6 | mobile 表格硬橫捲：`.tf-section table{{min-width:640px}}` 在 375px 下體驗差，但目前是刻意選擇（保可讀性優先於美觀） | 標記為**可接受的暫時取捨**，不列入本輪修復——改法（如關鍵欄位優先/卡片化表格）成本較高，先確認是否有買家/評審實測反饋再排 | 無 | （不驗收，留待下一輪視反饋決定） |
| 7 | 資訊卡 vs 可點卡視覺不分（多幣卡修完會變可點，需與純資訊卡如「怎麼運作」步驟卡區隔） | 可點卡追加輕量視覺提示：右下角小箭頭圖示或 hover 時邊框變 `#1f6feb`（呼應既有 CTA 藍） | `web.py` CSS `.tf-overview-card:hover` | 首頁同時看多幣卡（hover 有變化）與步驟卡（hover 無變化）→ 使用者能分辨哪個可點 |

**驗收基準**：evidence `<details>` + 真來源連結目前做對，改動不得破壞這塊。

---

## Follow-up（技術債，非本輪修復）｜`:active` loading 不是真防重複送出

**背景**：codex MEDIUM 複審抓到 `web.py`（`_PAGE` CSS 註解區，`button[type=
submit]:active` 附近）曾誤稱「`/analyze` 是唯讀 GET，重複送出風險可接受」
暗示已防住重複送出——這個推論混淆了「GET 不寫入資料庫（沒有髒污/重複
扣款）」跟「防不防得住重複執行」，兩者不是同一件事。已修正為誠實聲明：
CSS `:active` 純粹是 zero-JS 架構下的 best-effort 視覺 loading 回饋，
**不保證**防止使用者在導航完成前再次點擊/送出。

**現況風險評估**：
- 現在生產是離線 sample、`llm_mode=off`，重複送出頂多是白工重算一次
  確定性結果，$0 代價，殘餘風險可接受，**本輪不需要修**。
- **Bedrock 開啟後（`llm_mode=bedrock`）風險質變**：每次重複送出都是真實
  token 成本，`:active` 視覺回饋完全防不住連點/導航中再點造成的重複計費。

**Bedrock 正式開啟前必須做**（架構層級決策，CTO 不自行拍板，需老闆同意
方向後才動）：
1. **Server 端 idempotency**：例如以 `(client_ip, coin, query, 時間窗)`
   雜湊出的 key，短時間窗內重複請求直接回快取結果，不重跑 pipeline/不
   重打 Bedrock；或
2. **前端 JS 防重複**：submit 事件監聽 + disable，但會破壞現有 strict CSP
   （`default-src 'none'`）——需搭配 CSP 調整（如換用 nonce/hash 白名單），
   屬於架構抉擇。

兩條路都不在本輪快修範圍內。**追蹤**：[GitHub issue #51](https://github.com/cancleeric/trustforge/issues/51)，
Bedrock 上線排程前應重新拉出本節確認已處理。

---

## Phase 2｜核心戰略抉擇（需老闆拍板，CTO 不自行決定）

### a. 效度驗證 vs 誠實重定位
- **現況**：W4 回測 AUC≈0.49（隨機），連接器（`coingecko/news/onchain/prices/regulatory/social`）目前只 cache 現值、**無歷史資料落地**，無法立即用「真異質歷史」重跑驗證。
- **選項 1**：投入資源建歷史資料落地（定期快照 + 標記後續真實走勢），重新回測。優點：若通過，「信任分」名實相符；缺點：需要數週以上時間累積歷史樣本，黑客松時程內做不到。
- **選項 2（誠實重定位）**：短期內把「信任分」的宣稱從「預測市場方向」改為「結構化多源資訊彙整完整度 + 可追溯度」評分，不宣稱預測力。優點：立即可執行、不需新資料；缺點：產品故事弱化，需要重寫定位文案（CBO/CPO 協作）。
- **CPO 建議**：黑客松/近期展示先走選項 2（誠實），同時把選項 1 排進中長期 roadmap（需要真商用時間軸）。

### b. 資料密度（免費真源候選）
| 來源 | 成本 | Rate limit | 可行性 |
|------|------|-----------|--------|
| CryptoPanic 全量（現僅取子集）| 免費 tier | 依方案，需查當前 API 文件確認額度 | 高，改參數即可擴大覆蓋 |
| Etherscan 免費 API | 免費（需 API key）| 5 req/s（免費層，官方文件為準）| 中，需新增連接器邏輯 |
| 更多產業 RSS（交易所公告、監管機構）| $0 | 無明顯限制 | 高，成本最低 |
| X（Twitter）官方帳號 RSS 替代 reddit | 需第三方 RSS 橋接服務（多數免費層有限額）| 依橋接服務而定 | 中，需先驗證可用橋接服務是否穩定/免費 |

目標：從現況 5-10 源/7 證據拉到 20+，需老闆確認優先擴哪幾類（新聞類 vs 鏈上類 vs 社群類）。

### c. Niche 候選（窄到能誠實宣稱優勢，2-3 個）
1. **「證據可追溯的多源彙整工具」**：不比信任分預測力，比「每個結論都能點回原始來源」的透明度，對標 Nansen/Messari 的「黑箱結論」痛點。
2. **特定幣種/特定題型深耕**：例如只做「監管公告 + 官方公告」交叉驗證，範圍窄但每條都做深（而非現在 6 產品線都淺）。
3. **教育/新手向工具**：定位成「幫不熟悉多來源查證的使用者，把分散資訊組織成三層架構」，不跟專業分析平台拼深度，拼易用性。

---

## 三軸摘要

- **UI 快修**：7 項中 6 項可本輪派 CTO 執行，$0、有明確驗收 checklist；1 項（mobile 表格橫捲）建議暫不動，屬既有取捨非 bug。
- **核心戰略**：AUC≈0.49 是致命問題，但立即重跑驗證缺歷史資料基礎設施，時程上做不到；建議老闆在「誠實重定位」與「投入建歷史資料重新驗證」間拍板方向。
- **資料密度**：有具體免費源清單可擴充到 20+，執行門檻低，但仍需老闆定優先順序（新聞/鏈上/社群三選）。

## 建議老闆優先拍板的抉擇

**效度定位**：短期（含黑客松）是否同意「信任分」先誠實降階為「結構化彙整+可追溯」評分、拿掉預測力宣稱，同時把「真異質歷史重跑驗證」列入中長期 roadmap？這決定後續文案、demo 敘事、甚至產品名稱是否要調整，是本計劃中影響最大、必須老闆親自定調的一項。
