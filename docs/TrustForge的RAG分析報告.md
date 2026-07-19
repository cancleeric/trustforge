# TrustForge RAG 分析報告：現狀、落地方式與必要性評估

> 文件性質：專案內部技術分析（非科普）
> 彙整日期：2026-07-19
> 涵蓋範圍：RAG 在專案中的現狀實作、若要真正用上 RAG 的落地路線、以及是否有必要做的決策評估

---

## 摘要

本報告彙整三件事：

1. **現狀**：TrustForge 目前並無傳統向量 RAG，而是一道確定性、非證據性的「相似歷史題目檢索」（SQLite + 中文字元 bigram + Jaccard），結果只回前端展示，從未進 LLM 的證據鏈。
2. **若要做**：有兩條路線——路線 A（檢索結果進 prompt 當參考，不進評分）與路線 B（檢索知識進 Evidence 證據鏈）。兩者都受硬約束限制。
3. **必要性**：評估結論為**暫緩**——現狀 bigram 已稱職、產品護城河不依賴 RAG、RAG 檢索對象已被 ingestion 覆蓋、引入代價偏高。

---

## 第一部分：RAG 現狀實作分析

### 1.1 核心結論
- 專案**未使用** Chroma / FAISS / Pinecone / embedding 模型。
- 現有「RAG」= 輕量、確定性、可審計的「相似歷史題目檢索」，基於 SQLite + 中文字元 bigram + Jaccard 相似度。
- 檢索結果標記 `historical_non_evidentiary`（非證據性）：只作參考，不參與信任評分，不餵進 LLM 證據鏈。
- 刻意避開外部 embedding 服務，以保證可審計、可離線、成本可控。

### 1.2 核心實作（檔案：`src/trustforge/analysis_flow.py`）

| 位置 | 名稱 | 說明 |
| --- | --- | --- |
| `L50-55` | `_question_terms()` | 「語言無關檢索特徵」：中英混合問題的中文字元 bigram + 英數詞彙切分（取代 embedding） |
| `L58-62` | `_question_similarity()` | Jaccard 相似度 `|A∩B| / |A∪B|`（取代向量餘弦相似度） |
| `L323-376` | `AnalysisFlow.question_context()` | 主檢索函式：從 `analysis_questions` + `analysis_results` 取最近 300 筆，算相似度（同 coin +0.12、同 mode +0.08），回傳 `matches`、`conversation`、`retrieval="sqlite_char_bigram_v1"` |
| `L692` | `_stage_source_ingestion()` | 呼叫 `question_context(...)` 寫入 `package["retrieval_context"]`，日誌標記 `retrieval.question_memory` |
| `L737` | — | 結果進分析 payload 的 `retrieval_context` 欄位，回傳前端 |

**儲存層**：SQLite（`out/trustforge.sqlite3`），相關表 `analysis_questions`、`analysis_results`、`analysis_conversation`。

### 1.3 被呼叫的業務流程
- **分析管線（Hermes 分析）**：`analysis_flow.py:692` 每次分析 job 的「源收集」階段自動檢索相似歷史題目。
- **公開 API**：`src/trustforge/web.py:5254-5262` `_handle_api_analysis_question_context()` → 端點 `/api/analysis/question-context`；`web.py:6799` 路由分發。
- **前端**：`frontend/src/lib/endpoints.ts:103-109` `getAnalysisQuestionContext()`，強制校驗 `matches[].source_tier === 'historical_non_evidentiary'`。
- **自我優化（CEO sweep）**：`src/trustforge/improvement.py:181-187` 讀 `similar_question_rate` 產出 `question-retrieval-diversification` 提案。

### 1.4 向量庫 / 嵌入模型狀態
- **目前**：無向量庫、無 embedding 模型。
- **規劃中但被閘住**（`upgrade_control.py`）：
  - `L34` `rag-index`（Embedding 與索引策略）— `model-gate`（未實作）
  - `L35` `rag-reranker`（Reranker 與分面生成）— `model-gate`（未實作）
  - `L33` `question-rag`（題目 RAG 與對話記憶）— `reviewed-release`（已上線，即 bigram 方案）
- 全倉搜尋 `chroma / faiss / pinecone / sentence_transformers / langchain / llamaindex` 等**零命中**。

### 1.5 現狀卡點
`retrieval_context` 在 `analysis_flow.py:737` 只寫進回傳 payload，**從未傳入 `build_report`，也沒出現在任何 prompt**（`orchestrator.py:1016-1023` 的 prompt 只含 `claims` 與 `question`）。這是刻意的——避免歷史結論污染當下證據。

---

## 第二部分：若要真正用上 RAG —— 落地路線

### 2.1 硬約束（來自程式碼與文件）
- **所有模型呼叫只能走 AWS Bedrock**（`bedrock.py:1-5`），embedding 也必須是 Bedrock 模型（如 Titan Embed）。
- **非證據性資料不能進評分/證據鏈**：現有 `retrieval_context` 標 `historical_non_evidentiary`，前端強校驗。
- **客觀性反作弊**：只有 `OBJECTIVE_KINDS` 來源能標 `fact`（`bedrock.py:21`、`orchestrator.py:37`）。
- **可計費、可審計**：所有模型呼叫經 `estimate_cost` + `log.record_llm_cost`。
- **離線可跑**：`offline=True` 時須有降級（參考 `extract_claims_with_llm` 的 offline fallback）。
- **15 分鐘窗口 + 預算上限**：新步驟須納入 `budget_guard` 與 `log.remaining()`。
- **model-gate 閘通流程**：`rag-index` / `rag-reranker` 正式啟用須走 `reviewed-release` 流程。

### 2.2 路線 A：檢索知識當「參考上下文」（小改、低風險）
把向量檢索結果併進 prompt 的獨立區塊，**不進 Evidence 鏈、不進評分**。
1. 在 `bedrock.py` 加 `embed()`（Titan Embed）。
2. 在 `analysis_flow.py` 新增 `vector_search()` 取代 bigram，索引存 SQLite 或 OpenSearch Serverless。
3. 打通參數讓 `build_report` 收到檢索結果，在 `orchestrator.py:1016` 加 `<RETRIEVED_CONTEXT>` 區，維持 `non-evidentiary` 標記。
4. 前端校驗不變。

### 2.3 路線 B：檢索知識當「可評分證據」（深改、高風險）
讓外部文件走正規管線 `Document → claim 抽取 → score() → Evidence`：
1. 完成 `rag-index`（embedding + 索引）、`rag-reranker`（重排）。
2. 檢索到的 doc 當新 `Document` 丟進 `ingestion/collect()`，走 `bedrock.py:414` → `trust/scoring.py` 評分。
3. 嚴守反作弊：news/social 類檢索結果不能自動升 `fact`。
4. 計費/審計/預算/窗口全部納入。

### 2.4 架構決策點
最大決策是：檢索知識要定位為「參考上下文（仍 non-evidentiary，只進 prompt）」還是「可評分證據（進 Evidence 鏈）」。前者改動小、風險低；後者需先補 `rag-index`/`rag-reranker` 設計 spec（目前缺口）。

---

## 第三部分：必要性評估（是否有必要做）

### 3.1 現狀 bigram 檢索：「夠用」與「不足」的邊界
**夠用**：同/近字面複述題可命中；零外部依賴、零成本、零合規風險；職責（前端展示歷史相似題）本就不需要語義理解。

**不足**：
- 語義漂移（同義不同字面）：「BTC 會被監管打壓」vs「SEC 對 Bitcoin 的監管風險」Jaccard≈0，漏召回。
- 跨幣/同義映射：「ETH 會跟 BTC 一樣漲」vs「以太坊能否複製比特幣走勢」無法識別等價。
- 長文檔檢索不適用（當前檢索對象只是 question 短句）。
- 閾值脆弱（`analysis_flow.py:353` 硬編碼 `similarity <= 0.08` 丟棄）。
- 規模上限：最近 300 條全表掃 + 逐條 Python 算相似度（當前規模尚可）。

### 3.2 產品價值主張不依賴 RAG
價值主張（`docs/pitch/PITCH-1PAGER.md`、`COMPETITIVE-WHITESPACE.md`）：**可溯源證據鏈 + 反幻覺 + cross-source conflation 防禦**。三大支柱全由現有 ingestion + scoring + `Evidence` 支撐，與「檢索歷史題」或「embedding」無直接關係。現有 RAG 只屬「題面 UX 輔助」，不進證據鏈、不影響評分。

### 3.3 使用者痛點不存在
ingestion 來源（`ingestion/base.py:28`：`onchain`/`regulatory`/`hoyabit`/`news`/`social`，加 `prices`/`coingecko`）在每次分析時已 collect 並轉成 claims 進 prompt。RAG 若再檢索，檢索的也是同一批已 ingest 的文件——**重複檢索，不補任何缺口**。真正的「知識缺口」是歷史縱深，靠 `historical_sources.py` 的 backfill 解決，不是向量檢索；且 `news-rss-group`/`reddit` 仍 `archive_required`，先建向量庫是無米之炊。

### 3.4 成本與複雜度代價
- 工程：新增 Bedrock Titan Embedding 呼叫、向量索引存儲層與遷移、reranker 推理。
- 預算：每次 ingest 算 embedding + 檢索 reranker 都吃 `budget_guard` 日上限。
- 窗口/離線：15 分鐘窗口（`cli.py:64,95`）+ 離線模式（`bedrock.py:213-219`）下需 fail-safe 回退，否則破壞離線可用性。
- 合規：embedding 也須走 Bedrock，無法用本地輕量模型，等於每次付雲成本。
- `rag-index`/`rag-reranker` 仍 `model-gate` 且無 spec。

### 3.5 推薦結論
> **暫緩引入 embedding / 向量索引 / reranker。**

理由：
1. 現狀 bigram 已合格完成唯一職責，零成本零風險。
2. 產品核心價值不依賴 RAG。
3. 使用者知識需求已被 ingestion 覆蓋，RAG 不解任何痛點。
4. 引入代價高，且 `rag-index`/`rag-reranker` 仍 `model-gate` 無 spec。

**重新評估前提條件**：
- **路線 A 可考慮**：出現真實「同義題漏召回」用戶回饋，且只作前端提示/對話記憶、嚴禁進評分鏈；此時優先做輕量同義/幣種對照表（零 Bedrock 成本），不一定需要 embedding。
- **路線 B 不建議**：除非出現「長文檔語意問答」新需求 + 歷史歸檔就緒 + 補完 spec 過 model-gate + 通過預算衝擊測試；且即便如此，因檢索對象=已 ingest 文檔，邊際價值存疑。

### 3.6 一句話裁決
> 把 RAG 當「歷史題相似展示」的升級是**可選的 UX 優化**（且優先用同義映射表而非 embedding）；把它當「產品必需能力」是**誤判**——TrustForge 的護城河是可溯源證據鏈，不是檢索。**暫緩，把評審精力留給 evidence lineage 與 cross-source conflation 這些真正定義產品的部分。**

---

## 第四部分：Issue 盤點與相依性

### 4.1 現狀盤點（2026-07-19）
- **已實作、已結案**：`H-23`「Question RAG and Hermes dialogue memory」於 `docs/plans/HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md:217` 標記「完成第一版（2026-07-16）」——即現有的 bigram 相似題檢索（非證據性、不進評分鏈）。
- **已規劃但未排期、無 issue、無 spec**：`rag-index`（Embedding 與索引策略）、`rag-reranker`（Reranker 與分面生成）在 `src/trustforge/upgrade_control.py:34-35` 仍為 `model-gate`，僅列於升級清單，沒有對應 GitHub Issue，也沒有設計 spec。
- **相關安全 issue（已 CLOSED，非 RAG 實作）**：
  - `#191`：紅隊指出「外部歷史來源為 RAG 軟滲透面：檢索上下文未標 source_tier 且 text 無白名單」——正是推動 `retrieval_context` 標 `historical_non_evidentiary` 的由來，屬安全加固。
  - `#193`：live 模式 prompt injection，檢索上下文未與指令隔離。
- **結論**：目前沒有、也不該有「真正 RAG（embedding/向量）」的開發 issue，與本報告第三部分的「暫緩」結論一致。

### 4.2 相依性（若未來啟動 rag-index / rag-reranker）
啟動前必須先滿足的相依項，按阻塞順序：

1. **`rag-index` / `rag-reranker` 設計 spec 補齊**：目前兩者皆 `model-gate` 且無 spec，須先寫 `docs/architecture/` 下的 RAG 架構設計，才能走 `reviewed-release` 閘通（`upgrade_control.py:34-35`）。
2. **所有 embedding 呼叫只能走 AWS Bedrock**（`bedrock.py:1-5`）：須在 `BedrockClient` 新增 `embed()`（如 Titan Embed），遵守「唯一模型入口」硬約束；不能用本地/外部 embedding 服務。
3. **預算與執行窗口**：新增 embedding + reranker 須納入 `budget_guard` 日上限與 15 分鐘窗口（`cli.py:64,95`），且 `offline=True` 時須有 fail-safe 降級（`bedrock.py:213-219`）。
4. **歷史歸檔就緒**：`news-rss-group` / `reddit` 仍 `archive_required`（`historical_sources.py:16-17`）；若走路線 B（進證據鏈），長文檔語料須先歸檔完成，否則向量庫是無米之炊。
5. **反作弊約束**：只有 `OBJECTIVE_KINDS` 來源能標 `fact`（`bedrock.py:21`、`orchestrator.py:37`）；檢索到的 news/social 類不得自動升級為 fact。
6. **依賴 H-13a 實際覆蓋**：向量檢索的對象本質是已 ingest 的文檔，須先靠 `H-13a`（異質歷史序列 backfill，`docs/plans/HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md:102`）把來源覆蓋做實，否則 RAG 只是重複檢索同一批資料，邊際價值存疑。

### 4.3 決策記錄
- 本報告評估結論：**暫緩**引入 embedding / 向量索引 / reranker。
- 重新評估觸發條件：出現真實「同義題漏召回」用戶回饋（路線 A），或出現「長文檔語意問答」新需求且上述相依性全部滿足（路線 B）。

---

## 附錄：關鍵證據索引

| 主題 | 檔案:行 |
| --- | --- |
| bigram 特徵提取 | `src/trustforge/analysis_flow.py:50-55` |
| Jaccard 相似度 | `src/trustforge/analysis_flow.py:58-62` |
| 歷史題檢索 + non-evidentiary 標記 | `src/trustforge/analysis_flow.py:323-376, 366` |
| retrieval 僅進 payload 不進 prompt | `src/trustforge/analysis_flow.py:692, 737`；`src/trustforge/agent/orchestrator.py:1016-1023` |
| 唯一模型入口 = Bedrock | `src/trustforge/bedrock.py:1-5` |
| 客觀來源 kinds / 反作弊 | `src/trustforge/bedrock.py:21`；`src/trustforge/orchestrator.py:37` |
| Evidence dataclass | `src/trustforge/schema.py:34-82` |
| rag-index / rag-reranker = model-gate 無 spec | `src/trustforge/upgrade_control.py:34-35` |
| 離線模式 | `src/trustforge/bedrock.py:213-219` |
| 預算守衛 | `src/trustforge/budget_guard.py` |
| 15 分鐘窗口 | `src/trustforge/cli.py:64, 95` |
| 價值主張 | `docs/pitch/PITCH-1PAGER.md`；`docs/pitch/COMPETITIVE-WHITESPACE.md` |
| ingestion 來源覆蓋 | `src/trustforge/ingestion/base.py:28` 及各連接器 |
