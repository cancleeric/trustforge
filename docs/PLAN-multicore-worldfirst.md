# TrustForge 多核心世界第一擴充計劃

> CEO 指示（2026-07-03）：一個信任分核心撐不起世界第一，世界第一是多個核心疊起來。
> 本文件盤點「用現有資料就能做、$0、真、大廠也沒有」的新核心能力。**先 grep 實證再排序**，不改 code。

## 0. Grep 實證（現有基礎盤點）

| 基礎 | 實證 | 關鍵限制（grep 挖出，之前計畫未寫清楚） |
|------|------|------|
| Axis C 快照寫入者 | `scripts/fetch_scheduler.py --snapshot`（L44-48, L715-800）；`_snapshot_dict()` 存 `trust_score/direction/calibrated_confidence/decision_state/generated_at` | **只存最新一筆**：`cache.py` `DynamoDBCache` 表結構 PK=`source_id`、SK=`coin`（單一 item，無日期維度），`TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS=45min`，每輪覆寫。**沒有累積歷史**，`WORLD-FIRST-MASTER-PLAN.md` L145/372 已自認「P5 結果持久化 ❌未做，依賴持久化架構決策」、L154 附近「4.2 變動告警依賴 4.1 累積數天資料，尚無足夠天數」——本計畫候選#1與既有缺口**同一件事**，非新發現，是接下去做。 |
| W2 reputation_trace | `trust/scoring.py::_iterate_source_reputation()`（L926-1080）；生產唯一呼叫點 `orchestrator.py:807 dynamic_reputation=True`（**已上線**，非 PLAN-w2-wiring.md 舊狀態「尚待執行」——已執行） | **per-run 計算，不跨時間累積**：`_iterate_source_reputation` 是 bounded 迭代，僅吃「當次 claims」，`reputation_trace` 只存在單次 `/analyze` 或單次 snapshot cron 的 `pipeline.run()` 呼叫內，呼叫結束即丟棄，**沒有任何地方把它寫進 cache**——confirm：`fetch_scheduler.py` 的 `_snapshot_dict()` 沒有讀 `report` 裡的 `reputation_trace` 欄位。 |
| 14+ 資料源分 kind | `trust/scoring.py::KIND_REPUTATION`（L60-72）：9 種 kind（price 0.95/onchain 0.95/regulatory 0.90/hoyabit 0.85/news 0.65/social 0.35/price_live 0.90/sentiment 0.50/dev_activity 0.50）；`ingestion/news.py` 12 個 news source（11 RSS + CryptoPanic）、`onchain.py` 5 個、`regulatory.py` 1 個（SEC RSS）、`social.py` 1 個（Reddit）、`coingecko.py` 3 個 | 分佈**不均**：regulatory/social 各只有 1 個實體來源，若做「按 kind 拆維度」，這兩維度必須誠實標「單一來源，非多方佐證」，不能包裝成跟 news（12 源）同等級的可信度。 |
| web.py 呈現基礎 | `_render_overview_html()`（fetch_scheduler.py L656-715，5 幣卡片 grid）、`_render_trust_breakdown()`（web.py L1850 呼叫處）、`/status` 頁 | grep `排行\|ranking\|leaderboard` 全 repo **零命中**——目前無任何排序/排行 UI，總覽卡片是無序 grid，非排行榜。 |
| 信任分組成 | `ScoredClaim.components`（scoring.py L1279）含 `reputation/corroboration/rec/manip` 分項 | `_snapshot_dict()` 目前**不寫**任何分項到 snapshot（只寫 `trust_score` 總分），要做「操縱風險排行」需先在 snapshot 裡多存一個 `manip` 欄位（小改動，非架構級持久化）。 |

## 1. 候選新核心評估

### #1 歷史信任趨勢 Point-in-Time
- **世界第一理由**：Glassnode/CryptoQuant 有鏈上指標 PIT 歷史，但查無「新聞×鏈上×社群×監管綜合信任分」的 PIT 時間序列產品——這個資料型態本身就沒人做過（誠實揭露：未做過窮盡式競品調查，僅基於現有認知）。
- **現有基礎夠不夠**：**不夠**。Axis C 只存最新一筆（PK/SK 無日期維度、45min 覆寫），要做趨勢圖必須先讓寫入者「按日累積」而非覆寫。
- **$0**：是（DynamoDB 每日一筆小 JSON，量極小；或延用 JsonCacheBackend 本地檔案）。
- **CTO 工作量**：中——① `fetch_scheduler.py` 新增按日累積分支（不動既有 15min/45min 覆寫 key，另開 `source_id` 如 `__trust_snapshot_daily__`，SK 帶日期）；② `web.py` 新增趨勢頁（inline SVG sparkline，沿用既有 CSP-safe/dark theme 慣例）。
- **風險**：**時效風險最大**——今天上線也要等數天才有像樣的趨勢可展示，demo/評審時間點若在上線初期會是空趨勢。
- **需持久化**：**是**，且是全新的「按日累積」持久化（跟現有「單筆覆寫」不同機制），對應 CEO 講的 #10。

### #2 多維度信任拆解（信任雷達）
- **世界第一理由**：現有信任分產品多半給單一總分；把新聞信任/鏈上信任/社群信任/監管信任拆成雷達圖，目前未見同類「信任維度雷達」公開產品（同上，非窮盡調查）。
- **現有基礎夠不夠**：**夠，且現在就能做**。`KIND_REPUTATION` 已分好 9 種 kind、`ScoredClaim` 每筆帶 `doc.kind`，只要在既有單次 pipeline 結果上按 kind 分組聚合（不需新資料源、不需等待時間）。
- **$0**：是，純 CPU 運算，複用既有 real-off pipeline。
- **CTO 工作量**：小-中——`scoring.py` 新增「依 kind 分組聚合」子函式（從既有 `aggregate()` 邏輯抽出可重用部分）+ `web.py` 新增雷達/長條圖區塊（inline SVG）。
- **風險**：低，但**必須誠實揭露單一來源維度**（regulatory/social 各僅 1 個實體來源），否則會給「四維度均等可信」的假象。
- **需持久化**：**否**——單次 request 內即可算完，現在就能做。

### #3 跨幣信任×操縱風險排行
- **世界第一理由**：CoinGecko Trust Score 是交易所流動性信任，非「新聞/鏈上/社群/監管綜合信任+操縱風險」排行；查無同類產品。
- **現有基礎夠不夠**：**接近夠**。Axis C 總覽已有 5 幣即時 `trust_score`，只是無序 grid、無操縱風險欄。需 `fetch_scheduler.py::_snapshot_dict()` 多存一個 `manip` 分項欄位（`ScoredClaim.components["manip"]` 已存在，只是沒寫進 snapshot）。
- **$0**：是。
- **CTO 工作量**：小——排序 UI + snapshot dict 加一欄位，屬呈現層擴充，不動架構。
- **風險**：低。
- **需持久化**：否，用「當下最新一筆」snapshot 即可做即時排行，不需歷史；跟 #1 是互補但獨立的兩件事（#1 是時間軸、#3 是同一時間點的橫向排名）。

### #4 資料源動態信譽榜（W2 隨時間）
- **世界第一理由**：TruthFinder/CRH 類學術方法存在，但「公開展示各新聞/RSS 來源信譽隨時間變化排行榜」在加密資訊領域未見公開產品。
- **現有基礎夠不夠**：**部分夠，部分不夠**。W2 `dynamic_reputation=True` **已在生產跑**（`orchestrator.py:807`），`reputation_trace` 每次 snapshot cron 的 `pipeline.run()` 都會算出來——但**呼叫結束即丟棄，從未寫進任何 cache**（grep 確認 `_snapshot_dict()` 沒讀這欄位）。要做榜單，需要：① 在 snapshot 流程把 `reputation_trace` 取出、② 比照 #1 做按日累積持久化。
- **$0**：是，複用既有 W2 計算，不多打任何 API。
- **CTO 工作量**：中-大——比 #1 多一層（先要把現有「算了就丟」的資料接出來，再疊按日累積），是四個候選中依賴最多的。
- **風險**：中——`_iterate_source_reputation` 是「當次 claims 的相對信譽」，不同天的 claims 組成不同，直接把每天的 SR 數字疊成時間序列，統計意義要說清楚（是「該來源在該次分析中的相對信譽」，不是絕對信譽常數），避免誤導成「這個來源被驗證過永久可信/不可信」。
- **需持久化**：**是**，且比 #1 多一道「先擷取 reputation_trace」的工程，是四者中相依最深的。

## 2. 排序：世界第一影響 × 可行性

| 排名 | 候選 | 世界第一影響 | 可行性（現在 vs 待持久化） | 綜合 CP 值 |
|------|------|------|------|------|
| 1 | #2 多維度信任拆解 | 中高（信任雷達，未見同類） | **現在就能做**，$0，小-中工 | 最高 |
| 2 | #3 跨幣信任×操縱排行 | 中（排行榜+操縱風險，未見同類） | **接近現在就能做**（小改 snapshot dict + 排序 UI） | 高 |
| 3 | #1 歷史信任趨勢 PIT | 高（PIT 信任分，差異化最大） | **需先做持久化**（按日累積），且上線後仍要等天數才有效果 | 中（時效風險拉低短期 CP） |
| 4 | #4 資料源動態信譽榜 | 高（信譽演化榜，差異化大） | **需求最深**：先接出 reputation_trace，再疊按日累積 | 中低（依賴最多，最晚能疊） |

## 3. 建議連環疊的前 3 個（最高 CP、$0、現有資料夠）

1. **#2 多維度信任拆解**——立即做，$0，現有資料完全夠，不需等待任何天數，先上線建立「一個信任分→多維度」的差異化敘事骨架。
2. **#3 跨幣信任×操縱排行**——緊接著疊，複用 #2 產出的分項邏輯（`manip`/kind 分項）+ 現有 Axis C 總覽 snapshot，小改 snapshot schema 即可有「橫向排行」。
3. 同步**啟動 #1 的持久化寫入**（先讓 `fetch_scheduler.py` 開始按日累積，即使 UI 還沒做）——因為 #1 有「上線後還要等天數」的時間成本，越早開始累積歷史，越早能疊出趨勢圖；UI（sparkline）可以晚一點做，但**寫入要現在就開始**，不然每晚一天開始，PIT 賣點就晚一天成立。

## 4. 誠實標記：需先做持久化（#10）才能疊的項目

- **#1 歷史信任趨勢**：需要「按日累積」寫入機制（現行 Axis C 是覆寫式，PK=`source_id`/SK=`coin` 無日期維度）——這正是 `WORLD-FIRST-MASTER-PLAN.md` 已列的「P5 結果持久化+主題重開」「4.2 變動告警依賴累積數天資料」缺口，本計畫是把它具體化成「先寫入、後 UI」兩階段。
- **#4 資料源動態信譽榜**：除了跟 #1 同樣需要按日累積，還多一道「W2 `reputation_trace` 目前算完即丟，要先接出來」的前置工程，是四者中依賴最深、最晚能疊上去的一個。
- **#2、#3 不需要新持久化**，可以立刻做，不受 #10 任務進度影響。
