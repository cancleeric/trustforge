# 架構與信任演算法設計

## 設計原則

1. **信任層是核心，不是後處理。** 多源資訊在進 LLM *之前*就先評分、加權、過濾。
2. **一切可溯源（provenance-first）。** 每個結論都能追回支撐它的原始來源與分數。
3. **AI 輔助決策，不代替決策。** 輸出帶信心區間與反方證據，給交易者判斷依據。
4. **AWS Bedrock 是唯一模型入口。** 全部 LLM 呼叫集中在 `bedrock.py`，方便競賽合規審查與換模型。

---

## 三層管線

### Layer 1 — Ingestion（多源輸入）

統一介面 `ingestion.base.Source`，每個來源輸出標準化 `Document`：

| 來源 | 連接器 | 信號類型 |
|------|--------|----------|
| 新聞 / RSS | `news` | 敘事、事件 |
| 社群 / X | `social` | 情緒、熱度、喊單 |
| 鏈上 on-chain | `onchain` | 大額轉帳、交易所流入流出 |
| HOYA BIT 行情 | `hoyabit` | 報價、深度、成交（企業數據，7/13 補規格）|
| 監管 / 公告 | `regulatory` | 政策、合規事件 |

> 所有連接器先以離線樣本（`demo/sample_data/`）實作，工作坊後接真實 API。

### Layer 2 — Trust（信任提煉 ★ 核心）

對每一條從 Document 抽出的 **Claim（主張）** 計算 `TrustScore`：

```
TrustScore = w_src · SourceReputation
           + w_corr · CrossSourceCorroboration
           + w_rec · RecencyDecay
           − w_manip · ManipulationPenalty
```

- **SourceReputation**：來源歷史可信度（白名單/黑名單 + 動態學習），鏈上 > 監管 > 主流新聞 > 匿名社群。
- **CrossSourceCorroboration**：同一主張被幾個**獨立**來源佐證（去除轉發回音室）。
- **RecencyDecay**：時效指數衰減，加密市場資訊半衰期短。
- **ManipulationPenalty**：拉盤喊單 / bot 轉發 / 情緒極化偵測（Bedrock judge 輔助）。

權重可調，預設見 `trust/scoring.py::DEFAULT_WEIGHTS`。
最終對 query 相關主張做信任加權聚合，產出 `TrustedBrief`（含支撐證據與反方證據）。

### Layer 3 — Agent（編排 + 溯源生成）

- 輸入：`TrustedBrief`（已加權、已附溯源）。
- Bedrock agent 生成市場分析，**強制引用** brief 中的 claim id → 輸出帶溯源。
- 產出：結論 + 信任分數 + 信心區間 + 反方證據 + provenance 鏈。

---

## 資料流（端到端）

```
query
  → ingestion.collect(query)        # List[Document]
  → trust.extract_claims(docs)      # List[Claim]
  → trust.score(claims)             # List[ScoredClaim]  ★
  → trust.aggregate(scored, query)  # TrustedBrief
  → agent.analyze(brief)            # Analysis (帶 provenance)
  → demo UI 呈現
```

## 為何不用內部電話總機（anemone）

集團慣例是新服務接 AI 走電話總機。**但本競賽明文「僅限 AWS 基礎模型」**，
故 TrustForge 在競賽期間直連 `bedrock-runtime`，所有呼叫集中於 `bedrock.py`。
競賽結束後若要產品化，再評估是否抽換成閘道。
