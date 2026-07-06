# 來源品牌化優化計劃（Evidence List：slug → 品牌名 + 原廠 LOGO）

> 背景：CEO 用真 Chrome 看生產 `/analyze` evidence 清單，發現①來源名顯示內部
> slug（工程師代號）②除 blockchain-info 外皆為字母徽章 fallback，不合商業級
> 標準。本計劃**先 grep 實證根因**，再列補齊方案。不改 code（CTO 執行）。

## 根因（grep 實證，2 個獨立問題）

1. **`web.py:1809`**：`<span class="tf-src-pill">{src_logo} {e(ev.source)}</span>`
   —— LOGO 有查 `source_logo_html()`，但**顯示文字直接印 `ev.source` 原始
   slug**，`brand_logos.py` 裡其實已有 `_SOURCE_DISPLAY_NAME` 白名單 dict，
   只是**從未被 web.py 呼叫**去替換顯示文字。這是老闆看到 slug 的直接原因。
2. **`brand_logos.py::SOURCE_LOGO_SVG`** 目前只收錄 `reddit`／`blockchain-info`
   兩個真官方 LOGO（simple-icons CC0），其餘一律落入 `_fallback_badge_html`
   單字母徽章。且 `_source_brand_key()` 把三個 coingecko-* 併成一個
   `"coingecko"` key、兩個 reddit-* 併成 `"reddit"` key，**顯示名粒度不夠**
   （CoinGecko 三個不同資料面向、兩個 subreddit 都各自顯示成同一個名字）。

## 完整 source slug 清單（grep 逐檔實證，12 個）

| slug | 連接器檔案 | kind |
|---|---|---|
| `coindesk` | news.py `CoinDeskRSSSource.name` | news |
| `decrypt` | news.py `DecryptRSSSource.name` | news |
| `cryptopanic` | news.py `CryptoPanicSource.name`（需 env token 才啟用） | news |
| `reddit-cryptocurrency` | social.py `RedditCryptoSource("CryptoCurrency")` | social |
| `reddit-bitcoin` | social.py `RedditCryptoSource("Bitcoin")` | social |
| `alternative-me-fng` | onchain.py `FearGreedSource.name` | onchain |
| `blockchain-info` | onchain.py `BlockchainInfoSource.name` | onchain |
| `sec-gov` | regulatory.py `SECRSSSource.name` | regulatory |
| `coingecko-price` | coingecko.py `CoinGeckoPriceSource.name` | price_live |
| `coingecko-sentiment` | coingecko.py `CoinGeckoSentimentSource.name` | sentiment |
| `coingecko-dev` | coingecko.py `CoinGeckoDevSource.name` | dev_activity |
| `ohlcv-csv` | prices.py `price_facts()` 寫死 `source="ohlcv-csv"`（HOYA BIT 官方基準） | price |

無 `hoyabit-spread`／`whale-alert` 連接器（grep 全庫確認不存在，勿虛構）。

## slug → 顯示名 + LOGO 狀態

| slug | 顯示名（白名單） | LOGO 狀態 |
|---|---|---|
| `coindesk` | CoinDesk | fallback（simple-icons 無收錄，已 grep slugs.md 確認） |
| `decrypt` | Decrypt | fallback（同上） |
| `cryptopanic` | CryptoPanic | fallback（同上） |
| `reddit-cryptocurrency` | Reddit · r/CryptoCurrency | **真官方**（simple-icons CC0，reddit 已上線） |
| `reddit-bitcoin` | Reddit · r/Bitcoin | **真官方**（同上，共用同一 SVG） |
| `alternative-me-fng` | Alternative.me · 恐懼貪婪指數 | fallback（無收錄） |
| `blockchain-info` | Blockchain.com | **真官方**（simple-icons CC0，已上線） |
| `sec-gov` | 美國 SEC | fallback（政府機關無 simple-icons 收錄；不建議硬套非官方鷹徽） |
| `coingecko-price` | CoinGecko · 即時報價 | 待補（見下） |
| `coingecko-sentiment` | CoinGecko · 社群情緒 | 待補（同一 LOGO） |
| `coingecko-dev` | CoinGecko · 開發活動 | 待補（同一 LOGO） |
| `ohlcv-csv` | HOYA BIT · 官方 OHLCV | fallback（自家資料無需外部品牌 LOGO，用中性徽章即可） |

**CoinGecko 待補說明**：simple-icons 無收錄，**不得**用 fallback 字母敷衍
（CEO 明確點名 CoinGecko）。CTO 執行時須先查 CoinGecko 官方 Brand/Media Kit
（coingecko.com 官網通常有公開品牌資源頁）取得官方 SVG／PNG，**核實授權條款**
（是否允許第三方引用標示資料來源）；若官方資源可合法內嵌 → 轉 inline SVG 走
既有 `_svg()` 白名單模式；若查無可合法使用的官方素材 → 誠實維持體面 fallback
徽章（不得用其他來源 LOGO 拼裝冒充），並在 code comment 記錄查證過程（比照
`brand_logos.py` 現有 docstring 對 reddit/blockchain-info 的舉證方式）。

## #24 授權鐵律

- 已上線兩個真 LOGO（reddit/blockchain-info）→ simple-icons CC0，授權清楚，
  維持現狀。
- CoinGecko → 需 CTO 實際查證官方資源授權條款後才能內嵌，**查無合法來源前
  一律 fallback**，不得先斬後奏放上未經授權的圖檔。
- 其餘 6 個 fallback（coindesk/decrypt/cryptopanic/alternative-me-fng/
  sec-gov/ohlcv-csv）**維持誠實徽章**，不強行找 LOGO 拼湊；徽章需比現況
  「單字母框」更體面：改用品牌色圓角徽章 + 前 2-3 字縮寫或通用圖示（新聞用
  📰、指數用 📊、官方資料用 ✓ 盾牌型），但**不得**做成看起來像官方 LOGO 的
  精緻設計（避免混淆為真官方識別）。

## 技術方案

1. **`brand_logos.py`**：
   - 新增/擴充 `_SOURCE_DISPLAY_NAME` 為**逐 slug**（非併key）白名單 dict，
     12 個 slug 全收錄，未知 slug 落 `source[:1].upper()` 純中性 fallback
     （不猜品牌）。
   - `_source_brand_key()` 保留（LOGO 仍可用同一張 CoinGecko/Reddit SVG），
     但顯示名改吃逐 slug dict，兩者分離。
   - 若 CoinGecko 官方 LOGO 查證通過，`SOURCE_LOGO_SVG["coingecko"]` 補上。
   - Fallback 徽章視覺升級（圓角+品牌色+短縮寫/圖示），inline SVG/span 沿用
     現有 CSP-safe 模式，零外部請求。
2. **`web.py:1809`**：改為呼叫新增的 `source_display_name(ev.source)`（或等
   義函式），取代直接印 `e(ev.source)`；未知 slug 顯示 `ev.source` 本身當
   最後防線（不會顯示空白，但正常路徑不會走到這裡）。
3. 新增/更新對應 pytest 快照測試，斷言 12 個 slug 皆不輸出原始 slug 字串。

## CTO 執行範圍

- 修 `web.py` 顯示邏輯 bug（優先，立即可做，$0 風險）。
- 擴充 `brand_logos.py` 顯示名白名單（12 slug 全覆蓋）。
- 查證 CoinGecko 官方 LOGO 授權並視結果決定內嵌或體面 fallback。
- Fallback 徽章視覺升級（設計走 claude.ai/design → Chrome MCP 截圖審 →
  實作，依工作區 UI 流程規範）。
- 補測試：evidence 渲染快照 + 12 slug 顯示名對照表測試。

## CEO 驗收標準

- Chrome 打開 `/analyze` evidence 清單，逐一展開 12 種來源：**不再出現任何
  原始 slug 字樣**，皆為品牌顯示名。
- Reddit/Blockchain.com 顯示真官方 LOGO（現況已符合，回歸測試防退化）。
- CoinGecko 三個 source 若查證通過 → 顯示官方 LOGO；若未通過 → 顯示體面
  徽章（非單字母裸字）。
- 其餘 6 個來源顯示體面 fallback 徽章，非裸字母、非誤導成官方 LOGO。

## 風險

- CoinGecko 官方素材授權查證可能無結果 → 不得因此拖延顯示名修正（web.py
  bug fix 與 LOGO 補強可分兩個 PR 各自上線）。
- 徽章視覺升級屬 UI 改版，須先過 claude.ai/design + Chrome MCP 流程再實作，
  避免又一輪「不合格視覺」。
- 顯示名白名單需與 QA 回歸測試同步更新，防止未來新增連接器（如未來的
  whale-alert）又漏掉品牌化直接印 slug。
