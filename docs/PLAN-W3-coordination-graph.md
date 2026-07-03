# W3 抗操縱升級到「協同行為圖」— 可行性評估（grounded）

> master 計劃 Axis B §3.3（gray 細案，回應 CEO 的「先 grep 實證再判」要求）。
> 狀態：**資料卡，帳號-內容二部圖在現有連接器上不可行**。比照 W4 conformal
> 誠實負結果格式，列出可做的小改進 + 真圖算法需要的資料 roadmap。
> 評估日期：2026-07。

---

## 一句話結論

master §3.3 原文寫「✅ 能做，`networkx` Louvain 皆為免費確定性圖算法」，
但這個判定**沒有先查我們的連接器實際回傳什麼資料**。實測結果：

1. 全部連接器的 `Document`/`Evidence` **完全沒有 author/account 級欄位**
   （`schema.py` 只有 `source`，語意是「媒體名/子版/API 端點」，不是「發文
   帳號」）。
2. 一次真實分析（`out/*/evidence.json` 實例）的證據總數是 **3～7 筆**，
   `source` 去重後最多 5-6 個相異值，且每個 `source` 通常只貢獻 1 筆文件
   （coingecko price/sentiment/dev 各 1 筆，onchain 各 1 筆）。
3. 就算 news RSS / Reddit search.rss 單次真的能撈到多筆（未套用關鍵字/幣
   種過濾前），節點粒度仍是「來源字串」（`coindesk`/`decrypt`/
   `reddit-cryptocurrency`/`reddit-bitcoin`/`sec-gov`…），全池子只有
   **10 個 Source 類別**，不是帳號。

帳號-內容二部圖 + Louvain 社群偵測需要「很多帳號」才能找出有意義的簇——
我們現有資料連「帳號」這個維度都不存在，节点數量級也差了 1-2 個數量級。
**在現有資料上做真協同圖，得到的會是「10 個節點的圖」，Louvain 跑出來
要嘛是全連通一坨、要嘛是零散孤立點，沒有統計意義的社群結構，等於為了
做圖而做圖、產出一個假的深度包裝**——違反 #24 誠實原則，跟 W4 conformal
的「pseudo-AUC≈0.49，coverage 達標但無真實判別力」是同一類問題。

**判定：資料卡，不可行（現階段）。**

---

## 逐檔實證（grep 結果）

| 檔案 | 回傳粒度 | 每次查詢筆數量級 | 有 author/account 欄位？ |
|------|---------|----------------|------------------------|
| `ingestion/news.py` | CoinDesk RSS / Decrypt RSS / CryptoPanic，`source` = 媒體名（3 個值） | RSS feed 全量後經 query/coin 關鍵字過濾，實測樣本 0-1 筆進最終證據池 | 否，只有 `source`（媒體名） |
| `ingestion/social.py`（reddit） | `RedditCryptoSource`，`source` = `reddit-cryptocurrency` / `reddit-bitcoin`（2 個值，**子版級**） | 同上，經 coin 關鍵字過濾 | 否——`_parse_reddit_rss` 只解析 title/selftext/link/ts，**完全不解析 `<author>`**（即使 Reddit Atom feed 原始資料可能帶 `/u/username`，目前 parser 直接丟棄，未提取進 `Document`） |
| `ingestion/regulatory.py` | SEC EDGAR Atom，`source` 固定 `sec-gov`（1 個值，官方單一源） | 單一機關來源，天然無「多帳號」概念 | 否，且概念上不適用（監管公告非個人帳號發文） |
| `ingestion/onchain.py` | Fear&Greed / Blockchain.info，`source` 固定 2 個 API 端點值 | 每次固定 1-7 筆（F&G 近 7 天）+ 1 筆（BTC 統計） | 否，API 聚合數據，無發文者概念 |
| `ingestion/coingecko.py` | price/sentiment/dev，`source` 固定 3 個值（`coingecko-price`/`-sentiment`/`-dev`） | 每幣每類 1 筆 | 否，API 端點數據，無發文者概念 |
| `schema.py` `Document`/`Evidence` | 欄位：`source`/`source_url`/`kind`/`text`/`ts`/`meta` | — | **確認無** `author`/`account`/`username`/`user_id` 任何欄位（`grep` 全 miss） |
| `trust/scoring.py` `_coordination_*` | 見下節 | — | 現有訊號本就是 **來源字串級**（`c.doc.source`），非帳號級 |

真實產出樣本（`out/btc/evidence.json`、`out/real_btc/evidence.json`）：
**7 筆證據、5 個相異 source**（`ohlcv-csv`×3、`glassnode`、`hoyabit-ticker`、
`coindesk`、`x-anon-42`）——`x-anon-42` 這類唯一標記反而暴露了現行架構本來
就沒有「多帳號池」的設計預期（單一匿名占位值，不是可分群的帳號群體）。

---

## 現有 `_coordination_*` 訊號現況（scoring.py L419-648）

- **指標 A（已上線，informational-only）**：`_coordination_template_flags`
  —— 同議題跨 ≥3 個不同 `source`、Jaccard ≥0.8 才觸發，純文字相似度，
  不扣分（CEO 定案：無法區分協同 vs 合法聯播）。
- **指標 B（已寫完但停用）**：`_coordination_burst_flags` + 60 分鐘滾動視窗
  + 同窗中位數×3 倍門檻 baseline —— 經 4 輪 codex 對抗審修正仍持續挖出
  subtle 缺陷，`_coordination_signals` 明確註解「降級 follow-up #15，暫不
  啟用」，程式碼保留供未來重新設計參考。

兩者都是「**來源級**」統計，跟 SOTA「帳號級二部圖」是不同數量級的方法，
但也是我們資料唯一撐得起的層級。

---

## 誠實 roadmap（能做 vs 需要什麼資料）

### 現在能做（小改進，免費確定性，符合 #24 既有立場）

1. **#16 相似簇 flag 傳播**：`_coordination_template_flags` 目前只標「命中
   該筆 claim 自己」，可擴充成「同一相似簇內所有 claim 互相標記彼此」，
   讓 info_flag 完整反映簇成員關係，不需新資料、不改判定邏輯、維持
   informational-only 定調。
2. **#15 burst per-window 重新設計**：把已寫好但停用的
   `_coordination_burst_flags` 依 codex 對抗審已列出的缺陷逐項修正（固定
   牆鐘分桶可繞、baseline 對齊）——這是「來源級」而非「帳號級」的爆量
   偵測，資料現況完全撐得住，是 W3 在現有資料上唯一能往下深化的方向。
3. 兩者皆維持「informational-only，不扣信任分」的既有誠實立場，不新增
   `manip_penalty` 掛勾。

### 真協同圖需要但我們沒有的資料（roadmap，非本輪可排程）

- **帳號/使用者級 firehose**：如 Reddit 需要 OAuth API（`social.py` 註解已
  自陳「cloud IP 可能 403，生產可靠存取需 OAuth（待辦）」）才能穩定拿到
  夠多筆、且帶 `author` 欄位的貼文；X/Twitter 需要付費 API tier；這些都
  不是「免費確定性」範疇，需要另立合規/成本評估（比照 W1 AWS-only 紅線
  同等級的前置確認）。
- **足夠的帳號池規模**：Louvain 社群偵測要有統計意義，通常需要幾十到上
  百個節點；現況一次分析 3-7 筆證據、10 個 source 類別，即使拿到帳號級
  資料，量級也可能不足，需要先驗證「單一議題窗口內能收集到的帳號數」
  是否夠格，不能先建圖模組再回頭湊資料。
- **結論**：協同行為圖列入 **post-competition roadmap**，需求為「帳號級
  firehose 存取權 + 驗證量級足夠」，非本次黑客松/現行架構免費確定性
  範疇內可完成的項目。

---

## 附帶：W2 動態來源信譽 wiring

`docs/PLAN-w2-wiring.md` 方案完整（PR-A merge 引擎已就緒、PR-B 接線
`dynamic_reputation=True`），前提驗證已做（BTC 真樣本 confidence
0.6125→0.6279 可見變化），零額外 Bedrock 呼叫，**可行，建議與 W3 的
「#16/#15 小改進」一起排入本輪 CTO 執行**，不受 W3 資料卡結論影響
（W2 用的是既有 trust/reputation 機制，不依賴帳號級資料）。

---

## 建議行動

1. **不派** CTO 做「帳號-內容二部圖 + Louvain」——資料卡，做出來是假深度。
2. **可派** CTO 做 #16（相似簇 flag 傳播）+ #15（burst 重新設計，依 codex
   已列缺陷逐項修正）—— 兩者皆現有資料撐得住、免費確定性、不改變
   informational-only 定調。
3. **可派** CTO 一併執行 W2 wiring（`PLAN-w2-wiring.md` 現成方案）。
4. 更新 `WORLD-FIRST-MASTER-PLAN.md` §3.3：把「✅ 能做」改為「資料卡，
   降級為 #16/#15 小改進 + 帳號級圖列 post-competition roadmap」，避免
   後續規劃誤以為 Louvain 圖已排入可執行範圍。
