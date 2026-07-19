"""品牌 LOGO 白名單（inline SVG）——商業級視覺（Nansen/Messari 級）需求：
每個幣別 + 每個獨立來源放原廠 LOGO，純呈現層、$0，不打任何外部網路。

⛔ #24 鐵律：只用真官方 LOGO、不猜不偽造。SVG path data／品牌識別色一律
取自 simple-icons（CC0 授權，https://github.com/simple-icons/simple-icons，
develop 分支，2026-07 fetch）：
  - path data 取自各品牌 `icons/<slug>.svg`
  - hex 色碼取自 `data/simple-icons.json` 該品牌條目的 `hex` 欄位
    （品牌方指定的 monochrome 識別色，非隨手配色）

simple-icons **沒有收錄**的來源（CoinGecko／CoinDesk／Decrypt／
CryptoPanic／SEC EDGAR／Alternative.me／CoinTelegraph／Bitcoin Magazine／
CryptoSlate／Bitcoinist／NewsBTC／The Daily Hodl／The Block／U.Today／
Blockworks／mempool.space／Blockchair 等，已逐一 curl
`raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/<slug>.svg`
+ 查 slugs.md 確認不存在對應條目，2026-07 資料密度第一批／第二批 #24 補查）不得
瞎猜/拼裝其他來源的 LOGO 冒充官方——一律 fallback 成
中性的「圓角徽章 + 2-3 字縮寫」（見 `_fallback_badge_html`），顏色沿用
呼叫端既有語意色（如 evidence 的獨立性 tier 顏色），不偽裝成官方 LOGO、
不誤導使用者。

CoinGecko 官方 LOGO 查證記錄（docs/archive/plans/PLAN-source-branding.md #24 任務，
2026-07 查證）：CoinGecko 官方文件站 https://brand.coingecko.com 確有
公開「Attribution Guide」（`resources/attribution-guide`），**明文允許**
第三方在附上超連結＋文字歸屬（"Data provided by CoinGecko" 等）的前提下
使用其官方 LOGO，並提供「Brand Kit」下載官方 SVG/PNG。**但**該 Brand Kit
是透過 GitBook 檔案瀏覽器的網頁下載互動流程提供（`/files/<id>` 這類連結
會導回 GitBook SPA 頁面而非直接可 `curl` 到的靜態檔案 URL，不像
simple-icons 那樣有可直接 fetch 的 raw SVG 檔案路徑），本次查證環境沒有
瀏覽器工具可完成「點擊下載 ZIP → 解壓 → 核對向量路徑」這個流程，因此
**沒有辦法取得可驗證來源的真官方 SVG path data**——依 #24 鐵律「path
data 必須查證，不得憑記憶手繪」，這次維持 fallback 徽章，不強行湊一個
猜測的 CoinGecko 圖形。附帶查到官方核心品牌色 Gecko Green `#4BCC00`
（`visual-identity/color/core-brand-colors`），但**刻意不**拿來覆蓋
fallback 徽章顏色——理由同上一段：`_fallback_badge_html` 的顏色語意是
「沿用呼叫端既有 tier 色」，改用外部品牌色會讓使用者誤以為這是官方視覺
識別，因此保留現有設計原則不變。

CSP 相容性：本模組只產生 inline `<svg>`／`<span>` HTML 標記本身（非
`<img src=...>`、非外部 URL、非 `data:` URI），零外部請求。專案 CSP 為
`default-src 'none'`（見 `web.py::Handler._send`），該限制作用在瀏覽器
「要不要對外發請求抓資源」，inline 標記不觸發任何請求，因此無需、也不會
放寬 CSP header。

安全：所有 path data／hex／display name 皆為本檔案內的靜態白名單常數，
不接受任何呼叫端字串直接進 SVG——`coin_logo_html()`/`source_logo_html()`
一律先過白名單 dict 查找，查無白名單就是空字串或中性 fallback，永遠不會
把使用者可控字串直接嵌進 SVG 屬性。
"""

from __future__ import annotations

import html


def _svg(path_d: str, hex_color: str, title: str, viewbox: str = "0 0 24 24") -> str:
    """組一枚 inline monochrome 品牌 SVG。

    `fill` 用該品牌 simple-icons 指定的官方識別色；`<title>` 供螢幕閱讀器
    唸出品牌名（inline SVG 沒有 `alt` 屬性，`<title>` 是 SVG 標準的對應
    作法）。`path_d`/`hex_color`/`title` 都只會是本檔案內寫死的白名單
    常數（非使用者輸入），但仍過一次 `html.escape` 防禦性處理——多一層
    保險、零成本，不因為「反正是常數」就跳過。
    """
    d = html.escape(path_d, quote=True)
    color = html.escape(hex_color, quote=True)
    t = html.escape(title)
    return (
        f'<svg role="img" viewBox="{viewbox}" width="16" height="16" '
        f'xmlns="http://www.w3.org/2000/svg" fill="#{color}" '
        f'style="vertical-align:-3px;flex:none" aria-hidden="false">'
        f"<title>{t}</title><path d=\"{d}\"/></svg>"
    )


# ---------------------------------------------------------------------------
# 幣別 LOGO —— COIN_POOL 五幣（見 schema.py）皆有 simple-icons 收錄。
# ---------------------------------------------------------------------------

COIN_LOGO_SVG: dict[str, str] = {
    "BTC": _svg(
        "M23.638 14.904c-1.602 6.43-8.113 10.34-14.542 8.736C2.67 22.05-1.244 "
        "15.525.362 9.105 1.962 2.67 8.475-1.243 14.9.358c6.43 1.605 10.342 "
        "8.115 8.738 14.548v-.002zm-6.35-4.613c.24-1.59-.974-2.45-2.64-3.03l."
        "54-2.153-1.315-.33-.525 2.107c-.345-.087-.705-.167-1.064-.25l.526-2."
        "127-1.32-.33-.54 2.165c-.285-.067-.565-.132-.84-.2l-1.815-.45-.35 1."
        "407s.975.225.955.236c.535.136.63.486.615.766l-1.477 5.92c-.075.166-"
        ".24.406-.614.314.015.02-.96-.24-.96-.24l-.66 1.51 1.71.426.93.242-.5"
        "4 2.19 1.32.327.54-2.17c.36.1.705.19 1.05.273l-.51 2.154 1.32.33.54"
        "5-2.19c2.24.427 3.93.257 4.64-1.774.57-1.637-.03-2.58-1.217-3.196.8"
        "54-.193 1.5-.76 1.68-1.93h.01zm-3.01 4.22c-.404 1.64-3.157.75-4.05."
        "53l.72-2.9c.896.23 3.757.67 3.33 2.37zm.41-4.24c-.37 1.49-2.662.735"
        "-3.405.55l.654-2.64c.744.18 3.137.524 2.75 2.084v.006z",
        "F7931A",
        "Bitcoin",
    ),
    "ETH": _svg(
        "M11.944 17.97L4.58 13.62 11.943 24l7.37-10.38-7.372 4.35h.003zM12."
        "056 0L4.69 12.223l7.365 4.354 7.365-4.35L12.056 0z",
        "3C3C3D",
        "Ethereum",
    ),
    "SOL": _svg(
        "m23.8764 18.0313-3.962 4.1393a.9201.9201 0 0 1-.306.2106.9407.9407 "
        "0 0 1-.367.0742H.4599a.4689.4689 0 0 1-.2522-.0733.4513.4513 0 0 "
        "1-.1696-.1962.4375.4375 0 0 1-.0314-.2545.4438.4438 0 0 1 ."
        "117-.2298l3.9649-4.1393a.92.92 0 0 1 .3052-.2102.9407.9407 0 0 1 ."
        "3658-.0746H23.54a.4692.4692 0 0 1 .2523.0734.4531.4531 0 0 1 ."
        "1697.196.438.438 0 0 1 .0313.2547.4442.4442 0 0 1-.1169.2297zm-3."
        "962-8.3355a.9202.9202 0 0 0-.306-.2106.941.941 0 0 0-.367-.0742H."
        "4599a.4687.4687 0 0 0-.2522.0734.4513.4513 0 0 0-.1696.1961.4376."
        "4376 0 0 0-.0314.2546.444.444 0 0 0 .117.2297l3.9649 4.1394a.9204."
        "9204 0 0 0 .3052.2102c.1154.049.24.0744.3658.0746H23.54a.469.469 0"
        " 0 0 .2523-.0734.453.453 0 0 0 .1697-.1961.4382.4382 0 0 0 ."
        "0313-.2546.4444.4444 0 0 0-.1169-.2297zM.46 6.7225h18.7815a.9411."
        "9411 0 0 0 .367-.0742.9202.9202 0 0 0 .306-.2106l3.962-4.1394a."
        "4442.4442 0 0 0 .117-.2297.4378.4378 0 0 0-.0314-.2546.453.453 0 "
        "0 0-.1697-.196.469.469 0 0 0-.2523-.0734H4.7596a.941.941 0 0 0-."
        "3658.0745.9203.9203 0 0 0-.3052.2102L.1246 5.9687a.4438.4438 0 0 "
        "0-.1169.2295.4375.4375 0 0 0 .0312.2544.4512.4512 0 0 0 .1692.196"
        ".4689.4689 0 0 0 .2518.0739z",
        "9945FF",
        "Solana",
    ),
    "BNB": _svg(
        "M16.624 13.9202l2.7175 2.7154-7.353 7.353-7.353-7.352 2.7175-2.716"
        "4 4.6355 4.6595 4.6356-4.6595zm4.6366-4.6366L24 12l-2.7154 2.7164L"
        "18.5682 12l2.6924-2.7164zm-9.272.001l2.7163 2.6914-2.7164 2.7174v"
        "-.001L9.2721 12l2.7164-2.7154zm-9.2722-.001L5.4088 12l-2.6914 2.6"
        "924L0 12l2.7164-2.7164zM11.9885.0115l7.353 7.329-2.7174 2.7154-4."
        "6356-4.6356-4.6355 4.6595-2.7174-2.7154 7.353-7.353z",
        "F0B90B",
        "Binance",
    ),
    "XRP": _svg(
        "M5.52 2.955A3.521 3.521 0 001.996 6.48v2.558A2.12 2.12 0 010 11."
        "157l.03.562-.03.561a2.12 2.12 0 011.996 2.121v2.948a3.69 3.69 0 0"
        "03.68 3.696v-1.123a2.56 2.56 0 01-2.557-2.558v-2.963a3.239 3.239 "
        "0 00-1.42-2.682 3.26 3.26 0 001.42-2.682V6.48A2.412 2.412 0 015."
        "52 4.078h.437V2.955zm12.538 0v1.123h.437a2.39 2.39 0 012.386 2.4"
        "01v2.558a3.26 3.26 0 001.42 2.682 3.239 3.239 0 00-1.42 2.682v2."
        "963a2.56 2.56 0 01-2.557 2.558v1.123a3.69 3.69 0 003.68-3.696V14"
        ".4A2.12 2.12 0 0124 12.281l-.03-.562.03-.561a2.12 2.12 0 01-1.99"
        "6-2.12V6.478a3.518 3.518 0 00-3.509-3.524zM6.253 7.478l3.478 3.2"
        "59a3.393 3.393 0 004.553 0l3.478-3.26h-1.669l-2.65 2.464a2.133 2"
        ".133 0 01-2.886 0L7.922 7.478zm5.606 4.884a3.36 3.36 0 00-2.128."
        "886l-3.493 3.274h1.668l2.667-2.495a2.133 2.133 0 012.885 0l2.65 "
        "2.495h1.67l-3.494-3.274a3.36 3.36 0 00-2.425-.886z",
        "25A768",
        "XRP",
    ),
}


# ---------------------------------------------------------------------------
# 來源 LOGO —— 只收錄 simple-icons 有真實條目的品牌（見模組 docstring）。
# ---------------------------------------------------------------------------

SOURCE_LOGO_SVG: dict[str, str] = {
    "reddit": _svg(
        "M12 0C5.373 0 0 5.373 0 12c0 3.314 1.343 6.314 3.515 8.485l-2.28"
        "6 2.286C.775 23.225 1.097 24 1.738 24H12c6.627 0 12-5.373 12-12S"
        "18.627 0 12 0Zm4.388 3.199c1.104 0 1.999.895 1.999 1.999 0 1.10"
        "5-.895 2-1.999 2-.946 0-1.739-.657-1.947-1.539v.002c-1.147.162-"
        "2.032 1.15-2.032 2.341v.007c1.776.067 3.4.567 4.686 1.363.473-."
        "363 1.064-.58 1.707-.58 1.547 0 2.802 1.254 2.802 2.802 0 1.117"
        "-.655 2.081-1.601 2.531-.088 3.256-3.637 5.876-7.997 5.876-4.36"
        "1 0-7.905-2.617-7.998-5.87-.954-.447-1.614-1.415-1.614-2.538 0-"
        "1.548 1.255-2.802 2.803-2.802.645 0 1.239.218 1.712.585 1.275-."
        "79 2.881-1.291 4.64-1.365v-.01c0-1.663 1.263-3.034 2.88-3.207.1"
        "88-.911.993-1.595 1.959-1.595Zm-8.085 8.376c-.784 0-1.459.78-1."
        "506 1.797-.047 1.016.64 1.429 1.426 1.429.786 0 1.371-.369 1.41"
        "8-1.385.047-1.017-.553-1.841-1.338-1.841Zm7.406 0c-.786 0-1.385"
        ".824-1.338 1.841.047 1.017.634 1.385 1.418 1.385.785 0 1.473-."
        "413 1.426-1.429-.046-1.017-.721-1.797-1.506-1.797Zm-3.703 4.01"
        "3c-.974 0-1.907.048-2.77.135-.147.015-.241.168-.183.305.483 1."
        "154 1.622 1.964 2.953 1.964 1.33 0 2.47-.81 2.953-1.964.057-."
        "137-.037-.29-.184-.305-.863-.087-1.795-.135-2.769-.135Z",
        "FF4500",
        "Reddit",
    ),
    # blockchain-info（ingestion/onchain.py `BlockchainInfoSource.name`）
    # 是同一家公司（原域名 blockchain.info、後統一品牌為 Blockchain.com）
    # 的區塊鏈瀏覽器產品，simple-icons 收錄的條目名是 "Blockchain.com"
    # （slug: blockchaindotcom）——用的是同一個真官方品牌 LOGO，不是猜的。
    "blockchain-info": _svg(
        "M19.8285 6.6117l-5.52-5.535a3.1352 3.1352 0 00-4.5 0l-5.535 5.5"
        "35 7.755 3.87zm2.118 2.235l1.095 1.095a3.12 3.12 0 010 4.5L14."
        "22 23.3502a2.6846 2.6846 0 01-.72.525V13.0767zm-19.893 0l-1.09"
        "5 1.095a3.1198 3.1198 0 000 4.5L9.78 23.3502c.2091.214.4525.39"
        "14.72.525V13.0767z",
        "121D33",
        "Blockchain.com",
    ),
}


# 粗粒度品牌名（依 `_source_brand_key()` 正規化後的 key）——只給 fallback
# icon（`_fallback_badge_html`）的 tooltip／縮寫取名用，不是宣稱有官方 LOGO。
# 細粒度、給使用者實際閱讀的顯示文字見下方 `_SOURCE_DISPLAY_NAME_FINE` /
# `source_display_name()`。
_SOURCE_DISPLAY_NAME: dict[str, str] = {
    "coingecko": "CoinGecko",
    "reddit": "Reddit",
    "blockchain-info": "Blockchain.info",
    "alternative-me-fng": "Alternative.me",
    "sec-gov": "SEC EDGAR",
    "coindesk": "CoinDesk",
    "decrypt": "Decrypt",
    "cryptopanic": "CryptoPanic",
    "ohlcv-csv": "OHLCV",
    # 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）新增 6 家新聞 RSS，
    # simple-icons 逐一查證無收錄（見模組 docstring 查證記錄），一律走
    # 中性 fallback 徽章，不偽造官方 LOGO。
    "cointelegraph": "CoinTelegraph",
    "bitcoinmagazine": "Bitcoin Magazine",
    "cryptoslate": "CryptoSlate",
    "bitcoinist": "Bitcoinist",
    "newsbtc": "NewsBTC",
    "dailyhodl": "The Daily Hodl",
    # 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）新增 3 家新聞 RSS +
    # 3 個鏈上來源，simple-icons 逐一查證無收錄，一律走中性 fallback 徽章，
    # 不偽造官方 LOGO。
    "theblock": "The Block",
    "utoday": "U.Today",
    "blockworks": "Blockworks",
    # 2 個 mempool.space 連接器共用同一個品牌 key（見 `_source_brand_key`
    # 把 `mempool-space-*` 都正規化成 "mempool-space-fees"），這裡只需登記
    # 一筆，"mempool-space-difficulty" 不會被直接查到。
    "mempool-space-fees": "mempool.space",
    "blockchair": "Blockchair",
}

# fallback 徽章（icon）縮寫——2-3 字，非官方 LOGO 替代品，純視覺辨識用。
# 查無收錄的 key 由 `_fallback_badge_html` 退回取顯示名前兩碼，不會炸。
_FALLBACK_BADGE_ABBR: dict[str, str] = {
    "coingecko": "CG",
    "coindesk": "CD",
    "decrypt": "DE",
    "cryptopanic": "CP",
    "alternative-me-fng": "F&G",
    "sec-gov": "SEC",
    "ohlcv-csv": "HB",
    "cointelegraph": "CT",
    "bitcoinmagazine": "BM",
    "cryptoslate": "CS",
    "bitcoinist": "BI",
    "newsbtc": "NB",
    "dailyhodl": "DH",
    "theblock": "TB",
    "utoday": "UT",
    "blockworks": "BW",
    "mempool-space-fees": "MP",
    "blockchair": "BC",
}


def _source_brand_key(source: str) -> str:
    """把 raw `Evidence.source` 字串正規化成品牌查找 key。

    - `coingecko-price`/`coingecko-sentiment`/`coingecko-dev`（三個各自
      獨立的連接器，見 `ingestion/coingecko.py` 各 `Source.name`）共用
      同一個 CoinGecko 品牌。
    - `reddit-<subreddit>`（每個 subreddit 各自一個連接器 name，見
      `ingestion/social.py::RedditCryptoSource.name`）共用同一個 Reddit
      品牌。
    - `mempool-space-fees`/`mempool-space-difficulty`（`ingestion/onchain.py`
      2 個各自獨立的連接器，見模組頂部說明）共用同一個 mempool.space 品牌。
    - 其餘來源（`coindesk`/`decrypt`/`cryptopanic`/`sec-gov`/
      `blockchain-info`/`alternative-me-fng`/`ohlcv-csv`/`blockchair`，見
      `ingestion/{news,regulatory,onchain,prices}.py`）名稱本身即為 key。
    """
    if source.startswith("coingecko"):
        return "coingecko"
    if source.startswith("reddit"):
        return "reddit"
    if source.startswith("mempool-space"):
        return "mempool-space-fees"
    return source


# ---------------------------------------------------------------------------
# 使用者可見文字顯示名 —— 逐 slug 細粒度，跟上面 icon 用的粗粒度品牌 key
# 不是同一件事：3 個 coingecko-* 共用同一顆 LOGO icon，但文字要分辨清楚
# 「這是 CoinGecko 的哪個資料面向」，不能都印成同一句「CoinGecko」，
# 資訊量會不夠（見 docs/archive/plans/PLAN-source-branding.md 12 slug 對照表）。
# ---------------------------------------------------------------------------
_SOURCE_DISPLAY_NAME_FINE: dict[str, str] = {
    "coingecko-price": "CoinGecko · 即時報價",
    "coingecko-sentiment": "CoinGecko · 社群情緒",
    "coingecko-dev": "CoinGecko · 開發活動",
    "reddit-cryptocurrency": "Reddit · r/CryptoCurrency",
    "reddit-bitcoin": "Reddit · r/Bitcoin",
    "coindesk": "CoinDesk",
    "decrypt": "Decrypt",
    "cryptopanic": "CryptoPanic",
    "alternative-me-fng": "Alternative.me · 恐懼貪婪指數",
    "blockchain-info": "Blockchain.com",
    "sec-gov": "美國 SEC",
    "ohlcv-csv": "HOYA BIT · 官方 OHLCV",
    # 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）新增 6 家新聞 RSS。
    "cointelegraph": "CoinTelegraph",
    "bitcoinmagazine": "Bitcoin Magazine",
    "cryptoslate": "CryptoSlate",
    "bitcoinist": "Bitcoinist",
    "newsbtc": "NewsBTC",
    "dailyhodl": "The Daily Hodl",
    # 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）新增 3 家新聞 RSS +
    # 3 個鏈上來源。
    "theblock": "The Block",
    "utoday": "U.Today",
    "blockworks": "Blockworks",
    "mempool-space-fees": "mempool.space · 建議手續費",
    "mempool-space-difficulty": "mempool.space · 難度調整進度",
    "blockchair": "Blockchair · BTC 鏈上統計",
}


def source_display_name(source: str) -> str:
    """把 raw `Evidence.source`／連接器 slug 轉成使用者可讀的品牌顯示名。

    任何使用者可見處都應呼叫這支，不得直接印 `ev.source` 原始 slug——這
    正是老闆真 Chrome 看生產頁面時看到 `coingecko-sentiment`/`ohlcv-csv`
    這種工程師代號的根因（見 docs/archive/plans/PLAN-source-branding.md）。呼叫端仍須
    自行 `html.escape()`：本函式只負責取名，不負責跳脫（維持跟模組內其
    餘函式一致的分工，呼叫端如 `web.py` 一律用 `e()` 包一層）。

    1. 先查逐 slug 細粒度白名單 `_SOURCE_DISPLAY_NAME_FINE`（目前 12 個
       連接器皆已收錄，見上表）。
    2. 查無白名單（未來新連接器尚未補表，如 `whale-alert`）→ graceful
       降級：`-`/`_` 轉空白後 title case，至少不是原始裸 slug（例如
       `whale-alert` → `Whale Alert`），不崩、不洩漏內部命名慣例。
    """
    if source in _SOURCE_DISPLAY_NAME_FINE:
        return _SOURCE_DISPLAY_NAME_FINE[source]
    if not source:
        return "未知來源"
    return source.replace("-", " ").replace("_", " ").title()


def _fallback_badge_html(brand_key: str, color: str) -> str:
    """simple-icons 沒收錄的來源 → 中性「圓角徽章 + 2-3 字縮寫」。

    這**不是**官方 LOGO 的替代視覺、也不偽裝成官方 LOGO（#24 鐵律：不得
    放錯 LOGO，查無真 LOGO 寧可用明顯中性的縮寫徽章）。`color` 由呼叫端
    傳入（例如 evidence 既有的獨立性 tier 顏色，見 `web.py
    ::_independence_tier`），不硬編品牌色——硬編品牌色反而會讓使用者誤以
    為這是官方視覺識別（CoinGecko 官方色 `#4BCC00` 查證過程見模組頂部
    docstring，刻意不採用即是此故）。

    視覺升級（原本是單字母裸框，商業級要求體面一點）：改成填色圓角
    「chip」——背景用 `color-mix(in srgb, currentColor N%, transparent)`
    對呼叫端傳入的 `color` 取淡色調（`.tf-tier-pill` 既有同款手法，見
    `web.py` 樣式表），而不是自己另外調一個固定底色；`currentColor` 會
    自動吃到本 `<span>` inline `color:` 設的值，`color` 不論是 hex
    （`#3fb950`）或 CSS 變數（`var(--tf-muted)`）都能正確運作，不必自己
    手動做 alpha 混色字串拼接。
    """
    name = _SOURCE_DISPLAY_NAME.get(brand_key, brand_key)
    abbr = html.escape(_FALLBACK_BADGE_ABBR.get(brand_key) or (name[:2] or "??").upper())
    title = html.escape(name)
    c = html.escape(color, quote=True)
    return (
        f'<span class="tf-brand-fallback" title="{title}" '
        "style=\"display:inline-flex;align-items:center;justify-content:"
        "center;min-width:20px;height:16px;padding:0 5px;font-size:.62rem;"
        "font-weight:700;letter-spacing:.02em;border-radius:8px;"
        f'color:{c};background:color-mix(in srgb,currentColor 16%,'
        f'transparent);border:1px solid currentColor;vertical-align:-3px;'
        f'flex:none">{abbr}</span>'
    )


def coin_logo_html(coin: str) -> str:
    """幣別官方 LOGO；`coin` 不在白名單（非 `COIN_POOL` 五幣）回空字串——
    呼叫端據此不多印任何東西，不會因為缺 LOGO 而顯示破圖或錯誤幣的 LOGO。
    """
    return COIN_LOGO_SVG.get(coin, "")


def source_logo_html(source: str, fallback_color: str = "var(--tf-muted)") -> str:
    """來源官方 LOGO／中性徽章：優先真官方 SVG（simple-icons 收錄的品牌），
    查無則 fallback 成中性字首徽章（見 `_fallback_badge_html`）——永遠不會
    放上錯誤/瞎猜的 LOGO。
    """
    key = _source_brand_key(source)
    svg = SOURCE_LOGO_SVG.get(key)
    if svg is not None:
        return svg
    return _fallback_badge_html(key, fallback_color)
