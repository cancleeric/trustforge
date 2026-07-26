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

#### Trust Kernel（純計算核心 — #381）

Layer 2 的計算邏輯封裝為 **Trust Kernel**（`trust/kernel.py`），作為信任評分的
穩定介面層。Kernel 遵守**零外部依賴邊界**——禁止 IO/LLM/cache/boto3/env 存取，
確保所有計算可重現、可在純記憶體 fixture 中測試。

- 公開 API：`extract_claims()`, `score()`, `aggregate()`, `em_source_reliability()`
- 核心常數：`DEFAULT_WEIGHTS`, `KIND_REPUTATION`, `KIND_HALFLIFE_HOURS`
- 邊界文件：[`TRUST-KERNEL-BOUNDARY.md`](TRUST-KERNEL-BOUNDARY.md)

外層（`agent/orchestrator.py`、`pipeline.py`）透過 Kernel facade 存取信任評分功能，
`stance_fn`（需 IO/Bedrock）由外層注入。

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

## ModelHub 校準器候選編排（#351）

```text
data/training/{coin}.jsonl
  → flat loader + ≥100 unique labelled outcomes gate
  → chronological train/holdout split
  → label-free holdout payload + dataset SHA256
  → loopback-only ModelHub trigger/poll/artifact lookup
  → weighted ECE（baseline − candidate ≥ 0.02）
  → immutable proposal + execution log
  → per-coin current manifest
  → 人工審查／人工啟用（程式永不 automatic apply）
```

`modelhub_client.py` 僅接受 HTTP loopback host，停用 proxy/redirect；GET 有 bounded retry，
POST 不重試，並限制 timeout、poll deadline 與 response body。`modelhub_submit.py` 以 dir-fd
固定路徑元件、拒絕 symlink/non-regular input，且 file 與 directory fsync 成功後才發布
proposal/log/current。ModelHub 只收到 train rows 與不含 label/hit 的 opaque holdout features。

每次編排使用既有 `ExecutionLog` 的 15 分鐘預算；程式沒有修改 `budget_guard.py`。
ModelHub poll 本身最多 5 分鐘，且每一階段都再檢查剩餘 execution budget。五幣 live 模式
要求五個互異 `req_no`，避免不同幣種誤用同一 submission。

## SageMaker 訓練後端（#704）

ModelHub 與 SageMaker 是**同等級**的訓練平台（都有 GPU、都能跑 fit、都產 artifact），
由環境變數 `TRAINING_BACKEND=sagemaker|modelhub` 選擇後端。兩者不是上下游關係。

```text
推論端（不動）：Bedrock (LLM) + 本地 apply_calibration() (校準模型)
訓練端（可選）：ModelHub ←→ SageMaker（同等級、並行）
```

### 架構

```text
                    TrainingBackend Protocol
                    (training_backend.py)
                         │          │
              ┌──────────▼──┐  ┌────▼───────────┐
              │ ModelHub    │  │ SageMaker      │
              │ Backend     │  │ Backend        │
              └─────────────┘  └────────────────┘
```

### SageMaker 編排流程

```text
data/training/{coin}.jsonl
  → flat loader + ≥100 unique labelled outcomes gate
  → chronological train/holdout split
  → S3 upload (JSONL → s3://{bucket}/trustforge/training/{coin}/{ts}/input/)
  → SageMaker create_training_job
  → describe_training_job polling (max 300s)
  → download model.tar.gz from S3 ModelArtifacts
  → 解壓 model.json + SHA256 驗證
  → weighted ECE 比對（改善 ≥ 0.02）
  → immutable proposal + execution log
  → per-coin current manifest
  → 人工審查／人工啟用（程式永不 automatic apply）
```

### 環境變數

| 變數 | 說明 | 預設 |
|------|------|------|
| `TRAINING_BACKEND` | 訓練後端選擇 | `modelhub` |
| `SAGEMAKER_TRAINING_BUCKET` | S3 bucket | 必填 |
| `SAGEMAKER_ROLE_ARN` | Execution role | 必填 |
| `SAGEMAKER_INSTANCE_TYPE` | Instance type | `ml.m5.large` |
| `SAGEMAKER_USE_SPOT` | Spot instance | `false` |

### CLI

```bash
# SageMaker dry-run（不呼叫 AWS）
trustforge sagemaker-train --all --dry-run

# 單幣
trustforge sagemaker-train --coin BTC --dry-run
```

### 治理契約

- `automatic_apply: false`（與 ModelHub 一致）
- `requires_human_approval: true`
- candidate 不等於 activation
- artifact 視為 untrusted，下載後驗 SHA256 + `load_calibration_model()` 格式

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

---

## 方向判定邏輯（v0.16.21+ 修正）

**修正前**（bug）：用中文關鍵字匹配 → 全輸出「中性」
**修正後**：用 OHLCV 14 天報酬率計算

```
Layer 1（價格趨勢）：
  14天報酬率 > +3%  → 偏多
  14天報酬率 < -3%  → 偏空
  中間               → 中性

Layer 2（多源 stance 共識，待實作）：
  bullish 加權和 > bearish × 1.3 → 偏多
  bearish 加權和 > bullish × 1.3 → 偏空
  否則 → 中性

最終方向 = Layer 1（明確漲跌時）> Layer 2（價格中性/無法判定時補充）> 「不明」
```

審查標準：`.kiro/specs/direction-logic-338.md`
計劃：`docs/plans/PLAN-direction-logic-fix-338.md`
