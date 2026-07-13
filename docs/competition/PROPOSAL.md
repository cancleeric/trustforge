# TrustForge — 競賽企劃書
> 2026 雲湧智生黑客松 | HOYA BIT 命題 | 黑客組
> 版本：v1.0 | 撰寫：CPO / HurricaneSoft | 2026-07-01

---

## 1. 命題核心詮釋

### 評審在問的真正問題

「加密市場分析 AI Agent：多源資訊的信任提煉」——這道題的核心**不是**準確預測幣價，
也**不是**做最漂亮的 RAG 摘要，而是：

> **當資訊來源彼此矛盾、可信度未知時，AI 怎麼做出「有據可查、知道自己不確定」的判斷？**

這正是加密市場最難解的現實問題：一條新聞在 X 上被轉發 50,000 次，
但背後可能只有一個機器人農場；一篇分析師報告看似中立，卻引用了做市商委託的研究。
一般 RAG 系統對此毫無辨別力，TrustForge 的護城河就在這裡。

---

## 2. 產品定位

### 一句話定位

**TrustForge 是加密資訊的「信源熔爐」——把多源雜訊提煉成帶溯源、帶資訊完整度分級的市場判斷。**

### 對標比較

| 維度 | 一般 Crypto AI | TrustForge |
|------|--------------|------------|
| 信號輸入 | 餵給 LLM 後摘要 | 先逐條評信任分、加權後才進 LLM |
| 來源處理 | 不分等級 | 信譽 × 交叉佐證 × 時效 三維評分 |
| 操縱訊號 | 無 | 主動偵測喊單/bot 轉發，懲罰扣分 |
| LLM 職責 | 直接下結論 | 只負責「行文」——判斷結構由 pipeline 產出 |
| 輸出結構 | 一句話結論 | 事實層→推論層→結論，含資訊完整度分級+反方證據 |
| 可查證性 | 無 | 每個結論帶 claim_id → 原始來源 URL + 片段 |

### 差異化護城河：Trust Layer（信任層）

Trust Layer 是 TrustForge 唯一的「不可複製」核心。它在 LLM 接觸資料之前就：

1. 從每份文件抽出離散的 **Claim（主張）**
2. 對每條 Claim 計算四維 TrustScore：
   `TrustScore = w_src × SourceReputation + w_corr × CrossSourceCorroboration + w_rec × RecencyDecay − w_manip × ManipulationPenalty`
3. 把高信任 Claim 組成 **TrustedBrief（已加權摘要）**，帶溯源鏈
4. LLM 只能引用 TrustedBrief 的 claim_id 行文，**無法引入外部未經驗證的結論**

這個設計直接對應命題反作弊鐵則：
「市場判斷、證據整合、信任評分是我方 pipeline 的產物，不是把某分析網站的結論抄過來。」

---

## 3. 對評審的價值故事

### 問題陳述（共感）

加密市場每天產生數百萬條資訊：
- 80% 社群消息是情緒性噪音或機器人放大
- 20% 新聞彼此抄寫、同一來源多次引用
- 鏈上數據客觀但需要解讀脈絡
- 監管公告稀少但影響巨大

交易者或機構分析師面對的問題不是「資訊太少」，而是「哪條資訊該信？各自信多少？」

### TrustForge 的答案

TrustForge 不告訴你「BTC 明天會漲」——那是它刻意**不做**的事。
它告訴你的是：

- **此刻的市場判斷方向**（由 pipeline 產生，非 LLM 生成）
- **支撐這個判斷的是哪些高信任證據**（可點開看原始來源）
- **有哪些反方訊號被標記但未納入主結論**（透明的不確定性）
- **資訊完整度只有 0.62，因為鏈上資料在這次分析中只有 1 筆獨立來源**（明確說出限制）

> 「我們交付的不是一個答案，而是一個你能查證的答案。」

這個定位與 HOYA BIT「AI Native Exchange OS」理念高度一致：
**AI 是輔助決策的工具，不是代替決策的機器。**

---

## 4. 現場 Demo 敘事腳本

### 前提設定

- 執行環境：AWS App Runner（已部署），帶 Live Demo URL
- 真實 Bedrock 模型：`BEDROCK_MODEL_ID` 已設
- 多源連接器已接真實 API（Phase 2 完成後）
- 計時器可見（15 分鐘預算顯示在 Execution Log）

### 腳本流程（目標 12 分鐘內完成，留 3 分鐘緩衝）

---

**[T=0:00] 現場抽題**

主辦抽出，例如：**幣種 SOL｜題型：多源整合**
問題：「分析 SOL 過去兩週表現，整合價格/鏈上/新聞/社群，給整體判斷並說明各類資料一致程度」

---

**[T=0:30] 啟動分析，說明架構**

```
Live Demo URL → 選 SOL / multi_source / 貼入題目 → 送出
```

向評審說明：
「TrustForge 現在同時進行三層工作——Layer 1 平行抓取 5 類資料來源，
Layer 2 對每條主張計算信任分數，Layer 3 由 Bedrock 把已加權的可信摘要行文成報告。
整條鏈受 15 分鐘執行預算控管，Execution Log 每步都有時戳。」

---

**[T=1:00] Layer 1 — Ingestion 完成（約 1-3 分鐘）**

Execution Log 滾動顯示：
```
ingestion.prices     SOL_daily_ohlcv.csv  14 bars loaded       [T+0:12]
ingestion.hoyabit    hoyabit-api          ticker+depth ok      [T+0:18]
ingestion.onchain    coinglass-api        exchange-flow ok     [T+0:45]
ingestion.news       cryptopanic + rss    12 articles          [T+1:02]
ingestion.social     reddit r/solana      8 posts              [T+1:18]
ingestion.regulatory sec-rss              0 items (no event)   [T+1:20]
```

說明：「監管這次沒有新事件，我們標記為『無監管訊號』——而不是略過這個資料維度。
這個空白本身也是情報。」

---

**[T=3:00] Layer 2 — Trust 評分（<30 秒，純本地運算）**

```
trust.extract_claims   43 claims from 6 source types
trust.score            43 scored  (manipulation flagged: 2)
trust.aggregate        supporting=18 contrarian=7  confidence=0.71
```

說明：「你看到 2 條被操縱旗標的主張——它們來自匿名社群，包含明確喊單語意。
Trust Layer 把它們的分數壓到 0.12，移入 contrarian 列表，不會影響主結論，
但會出現在報告的『反方 / 低信任證據』區塊，讓讀者知道這些訊號存在。」

---

**[T=4:00] Layer 3 — Bedrock 行文（約 3-5 分鐘）**

說明：「注意——Bedrock 在這一步收到的不是原始新聞，
而是已被信任加權的 TrustedBrief，包含 claim_id 引用。
它的任務只是把推理寫成可讀敘述，並且只能引用 brief 中已有的 claim_id。
這是反作弊鐵則的技術保障。」

---

**[T=8:00] 報告出現，走 Evidence 抽查**

報告結構（評審可即時抽查）：

**第 1 節：結論 / 市場判斷**
「SOL 近兩週整體呈震盪偏空，鏈上大額流出訊號與交易所深度惡化同向，
主流新聞偏向觀望，社群情緒中性偏恐慌（Fear & Greed 38）。
整體資訊完整度：中（0.71），主要不確定性來自缺乏監管面新訊號。」

**第 2 節：關鍵依據（事實→推論→結論）**
每條帶 [E3][E7] 標記，點開對應 evidence.json：
```json
{
  "source": "coinglass-api",
  "fetched_at": "2026-08-01T09:23:00Z",
  "content_reference": "SOL exchange netflow -48,000 SOL (24h), 2026-07-29~2026-08-01",
  "related_claim": "鏈上出現大額淨流出交易所，潛在賣壓轉弱訊號",
  "trust": 0.87
}
```

**第 3 節：資訊完整度說明**
「監管資料本次無事件；社群樣本量 8 篇，不確定性偏高。
若出現高信任鏈上反轉（大額流入）或監管利好，結論需重評。」

---

**[T=10:00] 比較分析加演（如題型為 comparison）**

說明：「比較分析題型下，pipeline 對兩個幣種各跑一次完整管線，
輸出並列比較表：相對強弱、流動性差異、各類訊號一致程度。
這是對稱設計——不是先對 A 下結論再比 B。」

---

**[T=12:00] Execution Log 總覽**

```
total_elapsed: 11:42   budget_used: 78%   budget_ok: true
sources_fetched: 6   claims_scored: 43   evidence_items: 18
bedrock_calls: 3 (claim_extraction + judgment + narrative)
```

「11 分 42 秒，三次 Bedrock 呼叫，所有步驟有時戳，全程可審計。」

---

## 5. 與 HOYA BIT 企業理念連結

### HOYA BIT 定位

HOYA BIT 自定位為「AI Native Exchange OS」：
- AI 作為輔助決策工具，不代替決策
- 交易確認機制明確
- 資訊透明度優先

### TrustForge 的對齊

| HOYA BIT 理念 | TrustForge 實作 |
|--------------|----------------|
| 不代替決策 | 輸出資訊完整度區間 + 反方證據，明確說「這是輔助判斷」 |
| 決策確認機制 | `could_flip` 條件列表——交易者知道什麼情況下要重評 |
| 資訊透明度 | 每個結論帶 claim_id，回溯到原始來源 URL + 時間戳 |
| 企業數據整合 | HOYA BIT 行情/深度作為最高信任來源（kind=hoyabit, rep=0.85） |

### 商業應用路徑

**近期（黑客松後）：**
- HOYA BIT 將 TrustForge 信任層整合進其 AI 市場資訊服務
- 用戶看到的每條 AI 分析都帶「可信度評分 + 溯源按鈕」

**中期：**
- 白標（White-label）Trust Layer API，供其他交易所或資訊平台調用
- 收費模式：API 呼叫次數計費（按幣種 × 題型）

**長期：**
- 歷史信任分數資料庫——哪些來源在過去一年的準確率最高
- 自適應信譽系統（SourceReputation 動態更新）

---

## 6. 競爭優勢總結

### 評審評分點對應

| 評分項（權重）| TrustForge 的答案 | 競爭對手天花板 |
|-------------|-----------------|-------------|
| 主題切合 30% | Trust Layer 就是「信任提煉」的技術實作 | RAG 摘要，無來源區分 |
| 技術可行 25% | 三層 pipeline + Bedrock tool-use agent + AWS 架構 | 單次 ChatGPT API 呼叫 |
| 商業應用 20% | 可直接接入 HOYA BIT 企業數據，白標 Trust API | 無明確商業路徑 |
| 創意 15% | 操縱偵測 + 交叉佐證去回音室 + 資訊完整度區間 | 表面摘要 |
| 完成度 10% | 15 分鐘內完整輸出 4 交付件 | 通常只有報告 |
| AWS Kiro +10% | 採用 Kiro 作為開發 IDE，spec 生成 + 連接器規格 | 未採用 |

---

*文件路徑：`docs/competition/PROPOSAL.md`*
*對應文件：`docs/plans/DEV-PLAN.md`、`docs/competition/COMPETITION.md`、`docs/architecture/ARCHITECTURE.md`*
