# TrustForge 中的 RAG 實作分析

> 文件性質：專案內部技術分析（非科普）
> 最後檢視：2026-07-19
> 結論摘要：本專案目前並**未使用**傳統向量資料庫（Chroma / FAISS / Pinecone）或 embedding 模型。現有的「RAG」是一套輕量、確定性、可審計的「相似歷史題目檢索」，基於 SQLite + 中文字元 bigram + Jaccard 相似度。

---

## 1. 核心結論

TrustForge 目前的「RAG」實作本質上是：

- **確定性的「相似歷史題目檢索」**，而非基於向量空間的語義檢索。
- 用 **SQLite + 字元 bigram + Jaccard 相似度** 取代 embedding / 向量餘弦相似度。
- 檢索結果標記為 **`historical_non_evidentiary`（非證據性）**：只作為參考，不參與信任評分，不餵進 LLM 的證據鏈。
- 刻意避開需要外部 embedding 服務，以保證可審計、可離線、成本可控。

---

## 2. RAG 核心實作（檢索 / 嵌入替代方案）

檔案：`src/trustforge/analysis_flow.py`

| 位置 | 名稱 | 說明 |
| --- | --- | --- |
| `L50-55` | `_question_terms()` | 「語言無關檢索特徵」：中英混合問題的中文字元 bigram + 英數詞彙切分（取代 embedding） |
| `L58-62` | `_question_similarity()` | Jaccard 相似度 `|A∩B| / |A∪B|`（取代向量餘弦相似度） |
| `L323-376` | `AnalysisFlow.question_context()` | **主檢索函式**：從 `analysis_questions` + `analysis_results` 取最近 300 筆，計算相似度（同 coin +0.12、同 mode +0.08），回傳 `matches`、`conversation`、`retrieval="sqlite_char_bigram_v1"` |
| `L692` | `_stage_source_ingestion()` | 呼叫 `question_context(...)` 並寫入 `package["retrieval_context"]`，執行日誌標記為 `retrieval.question_memory` |
| `L737` | — | 結果進入分析結果 payload 的 `retrieval_context` 欄位，回傳前端 |

**儲存層**：SQLite（`out/trustforge.sqlite3`），相關表 `analysis_questions`、`analysis_results`、`analysis_conversation`（DDL 在 `L143-148` 與附近）。

---

## 3. RAG 被呼叫的業務流程

- **分析管線（Hermes 分析）**：`analysis_flow.py` 的 `_stage_source_ingestion`（L692）在每次分析 job 的「源收集」階段自動檢索相似歷史題目。
- **公開 API**：`src/trustforge/web.py`
  - `L5254-5262` `_handle_api_analysis_question_context()` → 直接呼叫 `flow.question_context()`，提供類似 `/api/analysis/question-context` 的端點（前端可獨立查詢）。
  - `L6799` 路由分發。
- **前端**：`frontend/src/lib/endpoints.ts`
  - `L103-109` `getAnalysisQuestionContext()` 型別與呼叫，並強制校驗 `matches[].source_tier === 'historical_non_evidentiary'`。
  - 用詞顯示為「相似歷史題目」（`frontend/src/components/GlossaryTerm.tsx` L3, L14；`HermesLeftRail.tsx` L110）。
- **自我優化（CEO sweep）**：`src/trustforge/improvement.py` `L181-187` 讀取 `similar_question_rate`，產出 `question-retrieval-diversification` 改進提案（題目檢索多樣化）。

---

## 4. 向量資料庫 / 嵌入模型狀態

- **目前**：無向量庫、無 embedding 模型。檢索 = SQLite + 字元 bigram + Jaccard。
- **規劃中但被閘住**（`upgrade_control.py`）：
  - `L34` `rag-index`「Embedding 與索引策略」— 狀態 `model-gate`（未實作）。
  - `L35` `rag-reranker`「Reranker 與分面生成」— 狀態 `model-gate`（未實作）。
  - `L33` `question-rag`「題目 RAG 與對話記憶」— 狀態 `reviewed-release`（已上線，即目前的 bigram 方案）。
- 全倉搜尋 `chroma / faiss / pinecone / sentence_transformers / langchain / llamaindex / CohereRerank` 等 **零命中**。

---

## 5. 相關文件

- `docs/RAG說明.md` — RAG 技術科普入門文（非實作文件）。
- `docs/architecture/HERMES-CONTINUOUS-INTELLIGENCE-2026-07-16.md` `L22-45` — 說明檢索端點用確定性 ranking 混合中英問題，且歷史結論明確為 `non-evidentiary`。

---

## 6. 總結

> 專案目前的「RAG」是一個**輕量、確定性、可審計的「相似歷史題目檢索」**：在 Hermes 加密貨幣分析流程的源收集階段，從 SQLite 撈取過往分析題目，用**字元 bigram + Jaccard 相似度**（而非 embedding / 向量庫）找出最相近的歷史問題與其已發布結論，作為**非證據性參考**附在分析結果裡（幫助使用者看連續性與過往覆蓋），**不參與信任評分、不餵進 LLM 的證據鏈**。真正的 embedding 索引（`rag-index`）與 reranker（`rag-reranker`）已在升級清單中被列為 `model-gate`，尚未實作。
