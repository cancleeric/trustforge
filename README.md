# TrustForge（信源熔爐）

> 加密市場分析 AI Agent — **多源資訊的信任提煉**
> 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽 ｜ 黑客組
> 命題：【智慧金融：HOYA BIT】加密市場分析 AI Agent：多源資訊的信任提煉
> 出品：HurricaneSoft（颶風軟體）

---

## 一句話

加密市場的資訊又多又雜、真假難辨（拉盤喊單、假新聞、機器人轉發）。
**TrustForge 不是「再問 AI 一次幣價」，而是把多源資訊先做「信任提煉」**——
對每一條主張評估可信度、做交叉佐證、保留溯源軌跡，最後輸出**信任加權**的市場分析。

> 我們交付的不是「一個答案」，而是「一個你能查證的答案」。

---

## 為什麼是這個切角（信任提煉 = 護城河）

評審看的是**技術深度 + 創意可用性**。一般做法的天花板是「RAG 餵料 → LLM 摘要」，
問題是：來源沒有可信度區分、單一來源造假無法察覺、結論無法溯源、無法落地給真實交易者用。

TrustForge 的差異化在中間那層 **Trust Layer（信任層）**：

| 一般 crypto AI agent | TrustForge |
|---|---|
| 多源 → 直接丟給 LLM 摘要 | 多源 → **逐條主張可信度評分** → 信任加權後才進 LLM |
| 來源不分等級 | **來源信譽 + 交叉佐證 + 時效** 三維評分 |
| 結論無溯源 | 每個結論帶 **provenance（溯源鏈）**，可點開看原始來源 |
| 一句話結論 | **信任分數 + 信心區間 + 反方證據**，給人決策而非代替決策 |

---

## 系統架構（三層）

```
        多源輸入                信任提煉 (核心)              Agent 編排 / 輸出
 ┌─────────────────┐     ┌──────────────────────┐     ┌────────────────────┐
 │ 新聞 / RSS       │     │ 1. 主張抽取            │     │ Bedrock Agent       │
 │ 社群 / X         │     │    (claim extraction) │     │  - 信任加權融合       │
 │ 鏈上 on-chain    │ ──▶ │ 2. 來源信譽評分        │ ──▶ │  - 帶溯源生成分析     │
 │ HOYA BIT 行情    │     │ 3. 交叉佐證 corroborate│     │  - 反方證據 / 信心區間 │
 │ 監管 / 公告      │     │ 4. 時效衰減 recency    │     │                      │
 └─────────────────┘     │ ⇒ TrustScore per claim│     └──────────┬─────────┘
                          └──────────────────────┘                │
                                                        ┌──────────▼─────────┐
                                                        │ Live Demo (Web UI)  │
                                                        │ 信任分數 + 溯源面板   │
                                                        └────────────────────┘
```

詳見 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## ⚠️ 競賽硬約束（務必遵守）

1. **僅限使用 AWS 服務提供之基礎模型** → 本專案**直連 AWS Bedrock**（`bedrock-runtime`）。
   - **不走集團電話總機 / anemone 閘道**（那是給內部產品用的）。競賽期間一律 Bedrock 直連。
2. 須使用 HOYA BIT 提供之企業數據（7/13 企業數據工作坊取得規格）。
3. 30 小時內（8/1–8/2）繳交：命題連結、企業數據應用、技術架構、生成式 AI 應用、**Live Demo**。
4. 可佈署實證、現場展示成果。

時程與須知見 [`docs/COMPETITION.md`](docs/COMPETITION.md)。

---

## 快速開始

```bash
# 1. 安裝
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. 設定 AWS Bedrock（競賽用 AWS 帳號）
export AWS_REGION=us-east-1
export BEDROCK_MODEL_ID="<8/1 現場公告的 Bedrock 模型 id>"   # 例：us.anthropic.claude-...

# 3. 跑一條 demo pipeline（離線樣本資料，不需 AWS 也能看信任層）
#    產出官方 4 交付件到 out/ ：report.md / evidence.json / execution_log.jsonl
python -m trustforge.cli analyze \
    --coin BTC --type multi_source \
    --query "分析 BTC 過去兩週市場狀況，整合多源資料" --offline --out out/btc

# 題型：multi_source（多源整合）/ hypothesis（假設驗證）/ comparison（比較分析）
# 幣種池：BTC / ETH / SOL / BNB / XRP

# 4. 測試
pytest -q
```

## 交付件（對齊官方）

每次 `analyze` 產出官方要求的 4 件（程式碼/設定即本 repo）：

| 交付件 | 檔案 | 內容 |
|--------|------|------|
| 分析報告 Final Report | `out/<coin>/report.md` | 結論/市場判斷 → 關鍵依據(事實→推論→結論) → 信心說明(限制) |
| 證據清單 Evidence List | `out/<coin>/evidence.json` | 每筆含 `source/fetched_at/content_reference/related_claim` |
| 執行紀錄 Execution Log | `out/<coin>/execution_log.jsonl` | 時戳 + 工具呼叫 + 流程，含 15 分鐘預算追蹤 |

> **反作弊鐵則**：市場判斷、證據整合、信任評分由本 pipeline 產生；Bedrock 只負責
> 把推理「行文」，不得把第三方現成結論當主要結果。詳見 `docs/COMPETITION.md`。

---

## 倉庫結構

```
trustforge/
├── README.md
├── ROADMAP.md              # 對齊黑客松里程碑
├── pyproject.toml
├── docs/
│   ├── COMPETITION.md      # 競賽時程 / 須知 / 約束
│   └── ARCHITECTURE.md     # 三層架構與信任演算法設計
├── src/trustforge/
│   ├── bedrock.py          # AWS Bedrock runtime 封裝（唯一模型入口）
│   ├── schema.py           # Coin 池 / 題型 / Evidence / Report（對齊交付規格）
│   ├── execlog.py          # 執行紀錄 + 15 分鐘預算追蹤
│   ├── ingestion/          # 多源連接器：prices(OHLCV) + news/social/onchain/hoyabit/regulatory
│   ├── trust/              # ★ 信任提煉引擎（本專案核心競爭力）
│   ├── agent/              # Bedrock 編排 → Report + Evidence + Log
│   └── cli.py              # demo / 競賽執行入口
├── demo/sample_data/       # 離線樣本：ohlcv/*.csv + 各來源 *.json
└── tests/                  # 13 測試（信任評分 / 價格 / 報告管線）
```

---

## 版控

- **GitHub（主）**：`cancleeric/trustforge`（private）
- **Gitea（鏡像）**：`http://YINGdeMacBook-Pro.local:3030/hurricanesoft/trustforge`

比照 agentic-os-console：GitHub 為主、Gitea 鏡像。
