# 架構與信任演算法設計

## 設計原則

1. **信任層是核心，不是後處理。** 多源資訊在進 LLM *之前*就先評分、加權、過濾。
2. **一切可溯源（provenance-first）。** 每個結論都能追回支撐它的原始來源與分數。
3. **AI 輔助決策，不代替決策。** 輸出帶資訊完整度分級與反方證據，給交易者判斷依據。
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
  - 產出：結論 + 資訊完整度分數 + 反方證據 + provenance 鏈。

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

## W3 前置：account 維度資料蒐集聲明（PR #107，harper CISO 審查附條件通過）

**蒐集目的**：目前累積帳號維度資料，供未來 W3「協同操縱偵測」演算法使
用（尚未實作，本 PR 純資料累積前置）。

**蒐集範圍**：僅 `Evidence.author`（型別 `str | None`，預設 `None`）——
來源平台**公開** username 原文字串（Reddit RSS/Atom `<author>`、新聞
RSS `<author>`/`dc:creator`），連接器選填寫入 `Document.meta["author"]`；
無作者概念的來源（多數 news、onchain、regulatory、hoyabit、price）此欄
位恆為 `None`，不補假值。收斂點 `agent.orchestrator._sanitize_author()`
對這個未經信任的上游輸入做健壯性守門：超過 200 字，或含 HTML 標籤
（`<`/`>`）／控制字元，整筆拒收（回 `None`），不折衷截斷。

**保留**：帳號維度資料只存在於 `Document.meta`/`Evidence.author`/每日
快照的 `"authors"` 鍵（`scripts/fetch_scheduler.py::_collect_authors()`
彙整），搭每日快照既有 90 天 TTL 一併淘汰，無獨立保留期限。

**不做的事**：不做任何跨平台關聯、不做任何衍生識別運算、不影響任何
`trust` 分數、不在任何 UI 顯示。

**對外邊界**：`author`/`authors` 僅存在於內部 cache/快照，供未來偵測用；
`/api/analyze`（含 `type=comparison` 模式）、`/analyze.json`、
`/api/overview`、`/api/history` 等公開（免認證，僅 rate-limit）JSON 端
點對外回應一律在序列化邊界過濾掉這兩個欄位（`web._public_evidence_dict()`
/ `web._public_snapshot_dict()`），`web.py` SSR 路由與 `lambda_handler.py`
（Lambda Function URL 生產入口）共用同一份 payload 組裝函式
（`web._build_analyze_json_payload()` / `web._build_comparison_json_payload()`），
不會把來源平台使用者名稱洩漏給任意呼叫端；內部資料本身不受影響。
TrustForge 沒有獨立的 `/api/compare` 端點，比較分析走的是
`/api/analyze?type=comparison`。

### 已知殘餘風險（W3 偵測/UI 上線前必須重新評估）

90 天為**被動** TTL（到期自然淘汰），目前**沒有**「來源平台使用者刪文/
改名/停權」與本地累積資料的**主動同步機制**——若使用者在來源平台刪除該
則貼文/留言，TrustForge 這邊累積到的 author username 仍會留到 TTL 到期
才消失。在 W3 偵測演算法或任何 UI 呈現正式上線前，必須重新評估是否需要
主動刪除同步（如定期比對來源是否還存在）或縮短 TTL，本 PR 範圍內不處理
（純資料累積前置，不含偵測/UI）。
