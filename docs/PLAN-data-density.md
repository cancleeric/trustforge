# 資料密度擴充計劃（#24）— 2026-07-03

> 老闆決策：先做本計劃，**再做 W2**。目標：一次分析證據源從 ~5-7 個拉到 **20+**，
> 讓「多源信任提煉」名副其實。全 $0、沿用現有 Source/safe_fetch/cache 架構。

## 1. 現況源清單 + 密度盤點（grep + 實測，2026-07-03）

| 檔案 | Source（`name`） | kind | 端點 | 狀態 |
|---|---|---|---|---|
| `news.py` | `coindesk` | news | coindesk.com/arc/outboundfeeds/rss | 200 OK |
| `news.py` | `decrypt` | news | decrypt.co/feed | 200 OK |
| `news.py` | `cryptopanic` | news | cryptopanic.com/api/v1/posts/ | **停用**（需 env `CRYPTOPANIC_TOKEN`，無 token 直接回空；`build_news_sources()` 條件式加入）|
| `social.py` | `reddit-cryptocurrency` | social | reddit.com/r/CryptoCurrency/search.rss | 實測**429**（老闆之前發現的問題本次重現）|
| `social.py` | `reddit-bitcoin` | social | reddit.com/r/Bitcoin/search.rss | 常態不穩，同一 UA 常被限流 |
| `onchain.py` | `alternative-me-fng` | onchain | api.alternative.me/fng/ | 200 OK |
| `onchain.py` | `blockchain-info` | onchain | api.blockchain.info/stats | 200 OK，僅 BTC |
| `regulatory.py` | `sec-gov` | regulatory | sec.gov EDGAR Atom | 200 OK，需含加密關鍵字才留 |
| `coingecko.py` | `coingecko-price/-sentiment/-dev` | price_live/sentiment | coingecko.com（keyless）| 200 OK，但同一 API 供應商，獨立性算 1 個第三方源 |

**盤點結論**：`base.collect()` 實際組裝來源 = 2 news（+cryptopanic 條件性）+2 social+2 onchain+1 regulatory+3 coingecko = 10 個 `Source` 物件，但 reddit 兩個常態 429/403 降級（`_failed` 清單吃掉），coingecko 3 個同源、cryptopanic 預設關閉 → **一次分析實際可見證據源常態只剩 5-7 個**，坐實老闆的觀察。獨立性也集中：news 僅 2 家媒體、regulatory 僅美國 SEC、社群僅 Reddit（還常掛）。

## 2. 可加的免費真源（已用 curl 逐一驗證 200 OK，2026-07-03 實測，非憑記憶）

### A. 新聞 RSS（最易，$0，不需 key）— 全部已驗證 200
| 來源 | RSS URL | 品牌 tier |
|---|---|---|
| CoinTelegraph | `https://cointelegraph.com/rss` | 中·社群（媒體）|
| Bitcoin Magazine | `https://bitcoinmagazine.com/feed` | 中·社群 |
| The Block | `https://www.theblock.co/rss.xml` | 中·社群 |
| CryptoSlate | `https://cryptoslate.com/feed/` | 中·社群 |
| Bitcoinist | `https://bitcoinist.com/feed/` | 中·社群 |
| NewsBTC | `https://www.newsbtc.com/feed/` | 中·社群 |
| The Daily Hodl | `https://dailyhodl.com/feed/` | 中·社群 |
| U.Today | `https://u.today/rss.php`（`/rss` 301 轉這裡，safe_fetch 逐跳驗證會自動處理，但白名單 URL 直接寫終點更省一跳）| 中·社群 |
| Blockworks | `https://blockworks.com/feed`（原 blockworks.co 308 轉此，同上建議直寫終點）| 中·社群 |

授權：RSS 為公開摘要用於索引/聚合，業界（Google News 等）慣例可用，僅取 title/link/摘要前 120 字（現有 `_parse_rss` 規格），不轉載全文，風險低。無 rate limit 硬性文件公告，比照現有 15 分鐘排程一批即可。

### B. CryptoPanic — 現況只是「未啟用」不是「只用部分」
`build_news_sources()` 只在 `CRYPTOPANIC_TOKEN` 存在時才加入；免費 API token 需到 cryptopanic.com 註冊（免費層，非付費）。**行動**：向 Eric 要一組免費 token 存 env，即可解鎖 1 源（含多家二手新聞聚合，等於間接擴增，但獨立性標「第三方聚合」不能標「官方」）。

### C. 鏈上免費（實測）
| 來源 | Endpoint | 備註 |
|---|---|---|
| mempool.space | `https://mempool.space/api/v1/fees/recommended`、`.../v1/difficulty-adjustment` | 200 OK，keyless，公開文件無嚴格 QPS 上限但建議 <10/min |
| Blockchair | `https://api.blockchair.com/bitcoin/stats` | 200 OK，免費層 1440 req/day（官方文件公告） |
| Etherscan | `https://api.etherscan.io/v2/api?chainid=1&...` | 實測**V1 已棄用**（回 `NOTOK: deprecated`），須改 V2 + 免費申請 apikey（免費層 5 req/sec）。**須先請 Eric 申請 key**，非 keyless。 |

CoinCap（`api.coincap.io`）實測連線失敗（該服務 2024 年底已停運），**不列入**，避免造假源。

### D. 社群（reddit 限流怎麼解）
- 現況 UA 已是描述性 UA，仍 429（實測重現）→ 純 RSS `.rss` 端點在 Reddit 對匿名/未認證流量的限流已收緊，非 UA 問題。
- **正解**：Reddit OAuth（`script` app 類型，`https://www.reddit.com/prefs/apps` 免費申請 client_id/secret），走 `oauth.reddit.com` API，官方免費層 100 QPM（每分鐘）。改動較大（新 auth 流程），列入「第二批」。
- **短期**：加 `r/ethereum`、`r/CryptoMarkets` 子版（已實測 `r/ethereum` 200 OK，`r/CryptoMarkets` 當下也 429——證明是全站限流非單一子版問題，**加子版無法繞過**，需靠降低排程頻率/加 delay 緩解，OAuth 才是根治）。
- X/Twitter 官方無免費 RSS；Nitter 實例多數已下線/不穩，**不建議**列入（違反「別造假源」精神，可用性太差）。

## 3. 架構相容（沿用現有，不需新框架）
- 每個新 RSS 源 = 一個 `Source` 子類（比照 `CoinDeskRSSSource`），複用 `news._parse_rss()`，`_fetch_url` 走 `safe_fetch.fetch_url`（白名單寫死終點 URL，含初始 URL 驗證 + DNS pinning，同現有慣例）。
- 新源進 `cache.DEFAULT_REFRESH_INTERVAL_SECONDS`：新聞類統一 15 分鐘（同 coindesk/decrypt）；鏈上類 15-60 分鐘視數據更新頻率。
- 品牌顯示：`brand_logos.py` 的 `SOURCE_DISPLAY_NAME`/`_SOURCE_ABBR`（無 logo 時退回縮寫徽章，如 simple-icons 未收錄的媒體）需逐一補新源 entry，呼應剛做完的 source-branding，不留「無名徽章」。
- 獨立性 tier：新聞源全部落入現有 `_COMMUNITY_KINDS`（中·社群），鏈上落入 `_THIRD_PARTY_KINDS`（高·第三方），**沿用 kind 分級，不必新增 tier 邏輯**。

## 4. Credit-safe
全部 keyless 或免費 token，$0；一律走 `fetch_scheduler.py` 排程 cache-only 模式，產品路徑零直接外呼，不會爆量、不碰 Bedrock。

## 5. 分批建議

**第一批（CP 值最高，CTO 範圍：news.py 加 6-9 個 RSS class + cache.py 加對應 interval + brand_logos.py 補顯示名）**
CoinTelegraph、Bitcoin Magazine、CryptoSlate、Bitcoinist、NewsBTC、Daily Hodl（6 家，全 200 OK 零風險）→ news 從 2 個變 8 個，單這批就能讓密度翻 4 倍。

**第二批**：The Block、U.Today（用 `/rss.php`）、Blockworks（用 `.com/feed`）+ mempool.space 2 端點 + Blockchair（onchain 從 2 變 5）。

**第三批（需 Eric 動作，非純技術）**：CryptoPanic token 申請、Etherscan V2 key 申請、Reddit OAuth app 申請 → 解鎖後 CTO 再排入開發。

**風險**：新聞源同質性高（多轉載同新聞），需 QA 驗證去重/獨立性不虛胖；來源網站可能未來改版斷 RSS（比照 CoinDesk 308 教訓，排程要能監控失敗率告警）；U.Today/Blockworks 走了一次 redirect 才拿到內容，建議直接把白名單 URL 寫終點，省一跳降低 SSRF 面。

**CEO 驗收**：Chrome 實測一次分析頁證據來源數（news 類）明顯從 2-3 增到 8+，各新源品牌名（非原始 `source` 字串）正確顯示，reddit 429 時整體分析不中斷（降級機制生效）。
