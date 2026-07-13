# TrustForge — 開發計劃（黑客松版）

> **已歸檔，不作進度依據。** 本計畫建立於 2026-07-01；其中大量核取項已完成、
> 被取代或依賴已變更。當前未完成工作、依賴與驗收條件唯一以
> [`HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md`](./HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md)
> 為準。保留本文僅供追溯原始黑客松需求與決策脈絡。

> 版本：v1.0 | 撰寫：CPO / HurricaneSoft | 2026-07-01
> 完成標準：8/1–2 決賽現場，抽題後 15 分鐘內跑出有真實多源證據的報告，且所有 Evidence 可被評審當場抽查

---

## 現況誠實評估（Gap Analysis）

### 逐評分項：現況 vs 目標

| 評分項 | 權重 | 現況 | 目標 | 差距 |
|-------|------|------|------|------|
| 多源整合 | 30% | OHLCV 是真；其餘 5 類各 1-2 筆假資料 | 5-6 類真實 API，每類多筆 | 致命——評審抽查 Evidence 一定發現 |
| 證據可回溯 | 30%（含） | 框架健全，但非價格證據無真實 URL/時戳 | 每筆 Evidence 帶可驗證 URL | 嚴重——結構到位，資料是假的 |
| 矛盾處理 | 30%（含） | contrarian 列表有，但輸入是合成資料 | 真實矛盾訊號被正確分層 | 中——邏輯對，缺真實資料驗證 |
| 信心校準 | 30%（含） | 演算法完整，confidence 有意義 | 同上，需真實輸入才能校準 | 中 |
| 限制說明 | 30%（含） | limits[] 正確填充 | 加入「此來源樣本資料」標注 | 輕微 |
| Agent 架構 | 25% | 單次 Bedrock complete()，非工具調用 | 多步 agent 推理（至少 2-3 步 Bedrock 呼叫）| 嚴重——命題明確要求推理+工具調用 |
| AWS 架構 | 25%（含） | AWS-ARCHITECTURE.md 文件完整 | 需要決賽簡報用的架構圖 | 輕微 |
| 執行穩定 | 25%（含） | 離線完整，在線缺連接器 | 真實 API 降級不崩 | 嚴重 |
| 商業應用 | 20% | 定位清晰，HOYA BIT 連結有說明 | 展示真實企業數據 | 中 |
| 創意 | 15% | Trust Layer 概念原創 | 操縱偵測需從 regex 升級 | 中 |
| 完成度 | 10% | 離線可跑完整管線 | 線上真實執行 15 分鐘內完成 | 中 |
| AWS Kiro | +10% | 未採用 | 納入開發流程並能在簡報中展示 | 完全缺失 |

### 優化點排序（影響 % × 投入比）

| 優先 | 項目 | 影響評分項 | 槓桿率 |
|------|------|----------|-------|
| P0 | 真實新聞連接器（免費 API）| 30% 主題 | 極高（低投入高分） |
| P0 | 真實鏈上連接器（免費 API）| 30% 主題 | 極高 |
| P0 | HOYA BIT 企業數據連接器 | 30% + 20% 商業 | 極高（等 7/13） |
| P0 | 多步 Agent 推理（Bedrock 3 步驟）| 25% 技術 | 高 |
| P1 | 比較分析雙管線實作 | 30% + 10% 完成度 | 高 |
| P1 | AWS Kiro 採用 + 文件 | +10% 加分 | 高（低投入） |
| P1 | Bedrock 語意 Claim 抽取（取代 regex）| 30% + 15% 創意 | 中高 |
| P2 | 社群連接器（Reddit + Fear&Greed）| 30% 主題 | 中 |
| P2 | 監管連接器（SEC RSS）| 30% 主題 | 中（低投入） |
| P2 | Demo UI 信任分數面板 | 20% 商業 | 中 |
| P3 | Bedrock 操縱偵測 Judge | 15% 創意 | 低（高投入） |
| P3 | DynamoDB 快取層 | 25% 架構 | 低（複雜度高） |

---

## 三個開發階段

### Phase 1｜工作坊前（7/1–7/12）
**目標：讓至少 3 類來源有真實 API，Agent 推理多步化，AWS Kiro 上手**

### Phase 2｜HOYA BIT 數據後（7/13–7/21）
**目標：接入企業數據，Bedrock Claim 抽取上線，比較分析完整實作**

### Phase 3｜決賽衝刺（7/22–8/1）
**目標：E2E 壓測，Demo 流暢度，簡報 AWS 架構圖，現場腳本演練**

---

## 詳細 Backlog

### Phase 1 Backlog（7/1–7/12）

---

#### [P0-1] 真實新聞連接器
**影響：主題切合度 30%（多源整合/證據可回溯）**
**投入：1 天**
**狀態：必做**

**目標：** `src/trustforge/ingestion/news.py` 產出真實 Document（有真實 URL、真實時戳）

**推薦 API（免費）：**
- CryptoPanic: `https://cryptopanic.com/api/v1/posts/?auth_token={TOKEN}&currencies=BTC`（免費 tier，不需 OAuth）
- CoinDesk RSS: `https://www.coindesk.com/arc/outboundfeeds/rss/`（公開無須 key）
- Decrypt RSS: `https://decrypt.co/feed`（公開）

**實作要點：**
```python
class NewsCryptoPanicSource(Source):
    kind = "news"
    name = "cryptopanic"
    def fetch(self, query: str, coin: str) -> list[Document]:
        # GET https://cryptopanic.com/api/v1/posts/?currencies={coin}
        # 回傳 Document(url=item['url'], ts=parse_ts(item['published_at']), ...)
```

**驗收標準：**
- [ ] `python -m trustforge.cli analyze --coin BTC --type multi_source --query "..."` 輸出的 evidence.json 中 news 類別有真實 URL（`https://cryptopanic.com/...` 或 coindesk.com）
- [ ] `fetched_at` 是真實時間戳（不是 0 或 sample 固定值）
- [ ] 評審打開 URL 可以看到對應文章

---

#### [P0-2] 真實鏈上連接器
**影響：主題切合度 30%（最高信任層來源）**
**投入：1-2 天**
**狀態：必做**

**目標：** `src/trustforge/ingestion/onchain.py` 帶真實鏈上數據（交易所流入流出）

**推薦 API（免費/低成本）：**
- CoinGlass: `https://open-api.coinglass.com/public/v2/indicator/exchange_flows`（免費公開 endpoint）
- Alternative.me Fear & Greed: `https://api.alternative.me/fng/`（完全免費，情緒指數）
- Blockchain.info（BTC only）: `https://api.blockchain.info/stats`（公開）

**Fear & Greed 優先實作**（最快，無 key，任何幣通用）：
```python
class FearGreedSource(Source):
    kind = "onchain"
    name = "alternative-me-fng"
    # GET https://api.alternative.me/fng/?limit=7
    # 產出 Document(text="加密市場恐懼貪婪指數：38（恐懼）", trust=0.80)
```

**驗收標準：**
- [ ] evidence.json 的 onchain 項目 `source_url` 指向真實 API endpoint
- [ ] `content_reference` 包含具體數值（如「Fear & Greed Index: 38, classification: Fear, 2026-08-01」）
- [ ] 可重複執行（非快取假資料）

---

#### [P0-3] 多步 Agent 推理（Bedrock 三步驟）
**影響：技術可行性 25%（「推理+工具調用+Agent 工作流程」）**
**投入：2-3 天**
**狀態：必做**

**目標：** 把現有的單次 `client.complete()` 改為 3 步驟顯式推理鏈，Execution Log 可見每步

**三步驟設計：**

```
Step 1: Claim Extraction（Bedrock 呼叫 #1）
  Input: raw documents from ingestion
  Prompt: "從以下多源資料中，抽出離散的市場主張（每條一個事實或觀點），JSON 格式輸出"
  Output: structured claims list (replaces regex sentence split)

Step 2: Judgment Formation（純 pipeline 計算，不呼叫 Bedrock）
  Input: scored claims from trust layer
  Output: TrustedBrief (direction, confidence, supporting/contrarian)

Step 3: Narrative Generation（Bedrock 呼叫 #2）
  Input: TrustedBrief with claim_ids
  Prompt: "用以下已加權可信摘要撰寫市場分析報告，每個判斷必須引用對應 claim_id"
  Output: final narrative

Step 4: Limitation Review（Bedrock 呼叫 #3，選用）
  Input: report draft
  Prompt: "審查此報告是否正確標注了所有資料不足與不確定性"
  Output: revised limitations section
```

**實作路徑：**
- 在 `bedrock.py` 新增 `extract_claims_with_llm(docs)` 方法
- 在 `agent/orchestrator.py` 把現有 `build_report()` 分拆成 3 個有明確 log step 的函式
- Execution Log 每步記錄 `bedrock_call_1`, `bedrock_call_2`, `bedrock_call_3` + 耗時

**驗收標準：**
- [ ] `execution_log.jsonl` 中可見至少 2 次 `bedrock.complete` 記錄，各有獨立時戳與輸入摘要
- [ ] Claim 抽取結果比 regex 切分更精準（測試：複雜英文/中文混合文件）
- [ ] 15 分鐘預算仍可達成（需壓測，Bedrock 呼叫時間加總 ≤ 8 分鐘）

---

#### [P1-1] 比較分析（comparison）真實實作
**影響：主題切合度 30% + 完成度 10%**
**投入：0.5 天**
**狀態：必做（題型池有 comparison，目前是 placeholder）**

**現況問題：** `orchestrator.py` 的 comparison 只回傳
`"{coin} 當前市場位置：... （比較分析需對每個幣種各跑一次 pipeline 後並列）"`

**目標：** 當 `qtype==COMPARISON`，自動解析出兩個幣種，各跑一次完整 pipeline，輸出並列報告

**實作要點：**
- `cli.py` 的 comparison 模式接受 `--coin BTC,ETH` 或從 query 解析「比較 BTC 與 ETH」
- `pipeline.py` 新增 `run_comparison(coin_a, coin_b, query)` → 各跑 `run()` → 合併輸出
- Report 新增比較表：相對強弱 / 流動性 / 各類訊號一致程度

**驗收標準：**
- [ ] `trustforge analyze --coin BTC,ETH --type comparison --query "比較 BTC 與 ETH 當前市場位置"` 可執行
- [ ] 輸出報告有並列比較章節
- [ ] evidence.json 包含兩個幣種的分別證據

---

#### [P1-2] AWS Kiro 採用
**影響：+10% 加分**
**投入：0.5 天（工具上手 + 文件）**
**狀態：必做（+10% 太重要）**

**AWS Kiro 是什麼：** AWS 的 AI 整合開發 IDE（2025 年發布），主打 spec-driven development——把需求描述生成正式規格（spec），再從規格生成程式碼骨架，支援 agent hook。

**TrustForge 採用策略：**

1. 安裝 AWS Kiro（VS Code extension 或 standalone）
2. 用 Kiro 生成 **HOYA BIT 企業數據連接器規格**（7/13 工作坊拿到 API 規格後，立刻用 Kiro 的 spec 功能產出 `ingestion/hoyabit.py` 骨架）
3. 用 Kiro Hook 驗證 Evidence 格式（每次 commit 確認 evidence.json 欄位完整性）
4. 在 `docs/architecture/AWS-ARCHITECTURE.md` 加入「開發期採用 AWS Kiro」段落 + 截圖
5. 決賽簡報包含「AWS Kiro 工作流程」一頁（spec → implementation → validation）

**驗收標準：**
- [ ] 有 Kiro 生成的 spec 檔或 hook 設定檔存在 repo
- [ ] `docs/architecture/AWS-ARCHITECTURE.md` 有更新的 Kiro 段落
- [ ] 決賽簡報有 Kiro slide

---

#### [P2-1] 樣本資料強化（過渡期）
**影響：完成度 10%（確保 offline demo 有說服力）**
**投入：0.5 天**
**狀態：加分（在真實 API 接通前使用）**

**目標：** 把 `demo/sample_data/*.json` 的每個來源從 1-2 筆擴充到 5-8 筆，
並且讓 URL/時戳格式像真實資料（不影響 Evidence 抽查——這是 offline 模式用）

**各來源擴充方向：**
- `news.json`: 5 條覆蓋 BTC/ETH/SOL/BNB/XRP 的不同立場新聞（正面/負面/中立）
- `onchain.json`: 4 條（大額流入交易所、大額流出、鯨魚地址移動、礦工拋售）
- `social.json`: 6 條（2 喊單被操縱旗標、2 理性分析、1 恐慌拋售、1 機構購入）
- `regulatory.json`: 3 條（ETF 核准、SEC 警告、國際監管）
- `hoyabit.json`: 4 條（ticker、深度惡化、成交量放大、買賣差擴大）

**驗收標準：**
- [ ] 離線模式 evidence.json 有 ≥20 筆，來源類型 ≥5
- [ ] 有至少 1 筆被操縱旗標（trust < 0.3）出現在 contrarian

---

### Phase 2 Backlog（7/13–7/21）

---

#### [P0-4] HOYA BIT 企業數據連接器
**影響：主題切合度 30% + 商業應用 20%（「用企業資料」是評分重點）**
**投入：1-2 天（依 7/13 工作坊取得的 API 規格而定）**
**狀態：必做（等 7/13 才能接）**

**7/13 工作坊前置清單（CEO 確認）：**
- [ ] 確認 API 類型：REST / WebSocket / 資料檔案下載
- [ ] 確認幣種覆蓋：5 個目標幣種是否都有
- [ ] 確認認證方式：API Key / OAuth / 公開
- [ ] 確認數據類型：實時行情 / 深度 / 成交 / 資金費率 / 歷史

**實作框架（7/13 後立即填入）：**
```python
# src/trustforge/ingestion/hoyabit.py
class HoyaBitSource(Source):
    kind = "hoyabit"
    name = "hoyabit-enterprise"
    # 待 7/13 填入真實 endpoint + auth
    def fetch(self, query: str, coin: str) -> list[Document]:
        ...
```

**使用 Kiro 生成 spec（7/13 當天）：**
- 把工作坊拿到的 API 文件輸入 Kiro → 生成 `ingestion/hoyabit.py` 規格 → 實作

**驗收標準：**
- [ ] Evidence.json 有 `kind=hoyabit` 且 `source_url` 指向 HOYA BIT API endpoint
- [ ] `content_reference` 包含交易對、時間範圍、具體數值（如「SOL/USDT bid-ask spread 0.12%, 2026-08-01T09:00Z」）
- [ ] 用 `live=1` 模式（真實 Bedrock）執行時 hoyabit 資料可見

---

#### [P1-3] Bedrock 語意 Claim 抽取
**影響：主題切合度 30%（「有層次的推理」）+ 技術 25%**
**投入：1 天**
**狀態：加分（比 regex 切句更有說服力）**

**現況：** `trust/scoring.py extract_claims()` 用 regex 按句號切分
**目標：** 用 Bedrock 呼叫 #1 做結構化主張抽取

**Prompt 設計：**
```
你是金融資訊分析師。從以下加密市場文件中，抽出每個獨立的「市場主張」。
每條主張格式：{ "claim": "...", "claim_type": "fact|inference|opinion", "direction": "bullish|bearish|neutral" }
只抽取可被獨立評估的主張，不要合并多個主張。
```

**驗收標準：**
- [ ] Claim 抽取結果有 `claim_type`（fact/inference/opinion）欄位
- [ ] fact 類 Claim 只來自 price/onchain/regulatory 來源
- [ ] 執行時間增加 ≤ 60 秒（比較 regex vs Bedrock 抽取）

---

#### [P2-2] 社群連接器（Reddit + Fear & Greed）
**影響：主題切合度 30%（社群是不可缺的來源維度）**
**投入：1 天**
**狀態：必做（若無社群連接器，多源整合少 1 維）**

**實作方案（免費）：**

Fear & Greed（已在 P0-2 快速實作）+ Reddit：
```python
class RedditCryptoSource(Source):
    kind = "social"
    name = "reddit-crypto"
    # GET https://www.reddit.com/r/CryptoCurrency/search.json?q={coin}&sort=new&limit=10
    # 無需 OAuth，User-Agent 設好即可（Reddit 公開 API）
```

**驗收標準：**
- [ ] evidence.json 有 `kind=social`，URL 指向 reddit.com 真實帖文
- [ ] 社群來源的 SourceReputation 正確（0.35），與高信任來源有明顯區分
- [ ] 操縱旗標正確觸發（若有喊單語意的 Reddit 帖）

---

#### [P2-3] 監管連接器（SEC RSS）
**影響：主題切合度 30%（監管是重要但低頻信號）**
**投入：0.5 天**
**狀態：加分（低投入，增加來源類型多樣性）**

**實作：**
```python
class SECRSSSource(Source):
    kind = "regulatory"
    name = "sec-gov-rss"
    # GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=&dateb=&owner=include&count=5&search_text=cryptocurrency&output=atom
    # 解析 Atom feed，篩選加密相關公告
```

**驗收標準：**
- [ ] evidence.json 有 `kind=regulatory`，URL 指向 sec.gov
- [ ] 無新監管事件時正確輸出「無監管訊號」到 limits[]

---

#### [P2-4] Demo UI 信任分數面板
**影響：商業應用 20%（可讀性 + 可採信）**
**投入：1 天**
**狀態：加分**

**目標：** Web UI（`web.py`）的分析結果頁面加入：
1. 來源信任分數橫條圖（各來源 TrustScore 分布）
2. 可展開的 Evidence 列表（點開看 content_reference + URL）
3. 操縱旗標醒目標記（紅色 badge）
4. 執行時間進度條

**技術：** 純 HTML + inline CSS（不引入任何 JS 框架，符合 stdlib 原則）

**驗收標準：**
- [ ] `/analyze` 頁面有 Evidence 可展開區塊
- [ ] 每筆 Evidence 有可點擊的 source_url
- [ ] 操縱旗標項目有視覺標記（`[MANIPULATED]` 或紅色背景）

---

### Phase 3 Backlog（7/22–8/1）

---

#### [P0-5] E2E 壓測與 15 分鐘預算驗證
**影響：完成度 10% + 技術 25%（穩定性）**
**投入：1 天**
**狀態：必做（最重要的 Demo 保險）**

**測試矩陣（對每個幣種 × 每個題型）：**

| 幣種 | 題型 | 目標時間 | Pass 標準 |
|------|------|---------|---------|
| BTC | multi_source | ≤ 12 分鐘 | 4 交付件輸出，evidence ≥ 8 筆 |
| ETH | hypothesis | ≤ 12 分鐘 | 同上 |
| SOL | comparison(BTC) | ≤ 14 分鐘 | 並列報告，evidence ≥ 12 筆 |
| BNB | multi_source | ≤ 12 分鐘 | 同上 |
| XRP | hypothesis | ≤ 12 分鐘 | 同上 |

**失敗降級驗證：**
- [ ] 某 API 超時 5 秒 → 該來源跳過，report.limits[] 標注「X 來源無法取得」，不崩潰
- [ ] Bedrock 回應延遲 → 有 timeout 處理，不無限等待

**驗收標準：**
- [ ] 5 種幣種 × 3 種題型的壓測結果有記錄（文件 `docs/qa/STRESS-TEST.md`）
- [ ] 無一次超過 15 分鐘
- [ ] 失敗降級不拋出未捕獲的 exception

---

#### [P0-6] 決賽 Demo 現場腳本演練
**影響：所有分項（Demo 流暢度影響評審主觀印象）**
**投入：0.5 天**
**狀態：必做**

**演練清單：**
- [ ] 完整跑一次 Demo 腳本（見 PROPOSAL.md 第 4 節），計時 ≤ 12 分鐘
- [ ] Evidence 抽查演練：評審指定任意一筆 Evidence，能在 30 秒內找到對應原始來源
- [ ] 比較分析題型演練（comparison）
- [ ] App Runner URL 確認有效，帶 `?live=1` 走真實 Bedrock
- [ ] 備案：若 Demo URL 失效，本機起 `python -m trustforge.web` 的備用流程

---

#### [P1-4] 決賽簡報 AWS 架構圖
**影響：技術 25%（決賽簡報必須含 AWS 架構圖）**
**投入：0.5 天**
**狀態：必做**

**簡報必要章節（命題要求）：**
1. 解題方向（信任提煉的核心概念）
2. AI 技術應用（Trust Layer 演算法 + 多步 Bedrock agent）
3. 數據資料應用（HOYA BIT 企業數據 + 5 類來源）
4. **AWS 架構圖**（可用 `docs/architecture/AWS-ARCHITECTURE.md` 轉為視覺圖）
5. AWS Kiro 開發流程（+10%）
6. Live Demo 網址 + 現場執行錄影連結

---

#### [P3-1] Bedrock 操縱偵測 Judge（選用）
**影響：創意 15%**
**投入：1 天**
**狀態：有餘裕才做**

**目標：** 把 `_manipulation_penalty()` 從 regex 升級為 Bedrock 二分類判斷：

```
Step: Manipulation Check（Bedrock 呼叫 #2.5，在 claim extraction 後）
Prompt: "判斷以下市場評論是否含有喊單/操縱意圖/機器人特徵。回傳 JSON: { is_manipulative: bool, confidence: float, reason: str }"
```

**驗收標準：**
- [ ] 對已知喊單語句（「快上車穩賺」「to the moon 百倍」）正確分類 = manipulative
- [ ] 對正當分析（「不排除短期回調可能性」）不誤判
- [ ] 不超過 15 分鐘預算（此步驟時間 ≤ 1 分鐘）

---

## 資源需求清單（CEO 確認）

| 資源 | 用途 | 費用估算 | 優先 |
|------|------|---------|------|
| CryptoPanic API token | 新聞連接器（免費 tier 夠用）| 免費 | P0 |
| 競賽 AWS 帳號（Bedrock 權限）| 線上模式運行 | 競賽提供或自費 | P0 |
| AWS Kiro 授權 | +10% 加分 | 免費（公測期）| P1 |
| X/Twitter API Pro | 社群連接器高品質版本 | $100/月 | P3（非必要） |
| CoinGlass API key | 更豐富的鏈上數據 | 免費 tier | P2 |

---

## 關鍵里程碑

| 日期 | 里程碑 | 驗收 |
|------|-------|------|
| 7/10 | P0-1 P0-2 完成（新聞+鏈上真實 API）| evidence.json 有真實 URL |
| 7/12 | P0-3 完成（多步 Agent 推理）| Execution Log 有 3 次 Bedrock 記錄 |
| 7/12 | P1-1 完成（comparison 真實實作）| comparison 題型可跑 |
| 7/12 | P1-2 完成（Kiro 採用）| spec 檔在 repo 中 |
| 7/13 | 工作坊：取得 HOYA BIT API 規格 | 填入 P0-4 實作 |
| 7/16 | P0-4 完成（HOYA BIT 連接器）| evidence 有 kind=hoyabit 真實數據 |
| 7/19 | P2-2 P2-3 完成（社群+監管連接器）| 6 類來源全有真實數據 |
| 7/21 | P1-3 完成（Bedrock Claim 抽取）| 3 步驟 agent 完整跑通 |
| 7/25 | P0-5 壓測完成 | 所有幣種 ≤ 15 分鐘 |
| 7/30 | P0-6 Demo 腳本演練完成 | 計時 ≤ 12 分鐘 |
| 8/1 | 決賽當天 | Live Demo + 4 交付件就緒 |

---

## 必做 vs 加分 摘要

### 必做（沒有這些主題分/技術分會大扣）
1. P0-1 真實新聞連接器
2. P0-2 真實鏈上連接器（至少 Fear & Greed）
3. P0-3 多步 Agent 推理（Bedrock 3 步驟）
4. P0-4 HOYA BIT 企業數據連接器（7/13 後）
5. P1-1 比較分析（comparison）真實實作
6. P1-2 AWS Kiro 採用（+10% 太重要）
7. P2-2 社群連接器（Reddit）
8. P0-5 E2E 壓測
9. P0-6 Demo 演練

### 加分（有餘裕再做）
1. P1-3 Bedrock 語意 Claim 抽取（提升創意分）
2. P2-1 樣本資料強化（離線 Demo 更有說服力）
3. P2-3 監管連接器（SEC RSS，低投入）
4. P2-4 Demo UI 信任分數面板（視覺說服力）
5. P3-1 Bedrock 操縱偵測 Judge（創意加分）
6. P3 DynamoDB 快取層（架構加分，但複雜度高）

---

*文件路徑：`docs/plans/DEV-PLAN.md`*
*對應文件：`docs/competition/PROPOSAL.md`、`docs/competition/COMPETITION.md`、`ROADMAP.md`*
