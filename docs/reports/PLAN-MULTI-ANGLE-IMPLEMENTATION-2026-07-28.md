# Multi-angle Analysis Orchestrator 擴充修改計劃

> 日期：2026-07-28
> 狀態：待裁示
> 依據：MULTI-ANGLE-FEASIBILITY-2026-07-28.md

---

## 概述

將 TrustForge 的「五個獨立分析視角」提升為「同輪五角度 → 確定性分歧偵測 → 綜合報告」。
核心差異化：交叉比對 100% 由確定性演算法完成，LLM 只負責把結果寫成人話。

---

## PR 拆分

### PR1：資料契約與 Synthesis 演算法

**新增檔案**：`src/trustforge/multi_angle.py`

**內容**：
- `AngleResult` dataclass — 單角度結果正規化結構
- `AngleConflict` dataclass — 角度間衝突描述
- `MultiAngleReport` dataclass — 五角度綜合報告
- `synthesize_angles(angles: list[AngleResult]) -> MultiAngleReport`
  - 方向背離偵測（兩兩比對）
  - 信心差距偵測（門檻 0.3）
  - 證據獨立性評估（Jaccard overlap）
  - abstain 保護（不硬拉 normal）
  - 共識推導（多數決 + 加權信心）
  - agreement_matrix 產出
- `angle_result_from_payload(mode, payload_json) -> AngleResult`
  - 從 `analysis_results.payload_json` 反序列化為 AngleResult

**測試**：`tests/test_multi_angle.py`
- 五角度全 normal、方向一致 → consensus 正確
- risk 偏空 + sentiment 偏多 → 產生 direction_divergence conflict
- fundamentals abstain → 綜合不得為 normal（退為 partial_abstain）
- 五角度都引用同一個 source → evidence_independence_warning
- 所有角度 abstain → 綜合為 full_abstain

**預計工作量**：2-3 小時
**依賴**：無

---

### PR2：後端 Multi-angle 入口與觸發

**修改檔案**：`src/trustforge/analysis_flow.py`

**新增**：
```python
def submit_multi_angle(self, coin: str, question: str, *,
                       locale: str = DEFAULT_NARRATIVE_LOCALE) -> dict:
    """建立同一個 snapshot，同時跑五角度。"""
    coin = coin.strip().upper()
    if coin not in COIN_POOL:
        raise ValueError(f"unsupported coin: {coin}")
    snapshot_id = self.create_snapshot(coin, query=question)
    job_ids = {}
    for mode, (qtype, template) in MODES.items():
        mode_question = template.format(coin=coin)
        self.register_question(coin, mode, mode_question, enqueue=False)
        job_id = self.enqueue_job(snapshot_id, mode, mode_question,
                                  origin="manual", locale=locale)
        job_ids[mode] = job_id
    return {"snapshot_id": snapshot_id, "job_ids": job_ids, "coin": coin}
```

**新增**：synthesis 觸發（在 `_stage_report_delivery` 結尾）
```python
def _maybe_trigger_synthesis(self, snapshot_id: str, coin: str) -> bool:
    """檢查同 snapshot 五角度是否全部完成，觸發 synthesis。"""
    completed = self._conn().execute(
        "SELECT DISTINCT mode FROM analysis_results WHERE snapshot_id=? AND coin=?",
        (snapshot_id, coin)
    ).fetchall()
    completed_modes = {row["mode"] for row in completed}
    if not MODES.keys() <= completed_modes:
        return False
    # 所有角度已完成，執行 synthesis
    from .multi_angle import angle_result_from_payload, synthesize_angles
    angles = []
    for mode in MODES:
        row = self._conn().execute(
            "SELECT payload_json FROM analysis_results "
            "WHERE snapshot_id=? AND coin=? AND mode=? "
            "ORDER BY published_at DESC LIMIT 1",
            (snapshot_id, coin, mode)
        ).fetchone()
        if row:
            angles.append(angle_result_from_payload(mode, row["payload_json"]))
    if len(angles) < len(MODES):
        return False
    report = synthesize_angles(angles)
    # 存入 analysis_results，mode='multi_angle'
    self._conn().execute(
        "INSERT OR REPLACE INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
        (f"result-ma-{snapshot_id}", f"ma-{snapshot_id}", snapshot_id, coin,
         "multi_angle", "五角度綜合評估",
         json.dumps(dataclasses.asdict(report), ensure_ascii=False),
         time.time())
    )
    return True
```

**新增 API**：`/api/multi-angle`（在 `web.py`）
- GET `?coin=BTC` → 回傳最新的 MultiAngleReport
- GET `?coin=BTC&snapshot_id=xxx` → 回傳指定 snapshot 的結果
- POST `{coin, question, locale}` → 呼叫 `submit_multi_angle`，回傳 job_ids

**預計工作量**：3-4 小時
**依賴**：PR1

---

### PR3：前端五角度總覽

**新增檔案**：
- `frontend/src/hermes/MultiAngleOverview.tsx`
- `frontend/src/hermes/ConflictBadge.tsx`
- `frontend/src/lib/multiAngleEndpoints.ts`

**MultiAngleOverview 設計**：

```
┌──────────────────────────────────────────────────────┐
│  BTC 五角度綜合分析          snapshot: snap-btc-a3f… │
├──────┬────────┬──────┬────────┬──────────────────────┤
│ 角度 │ 結論   │ 信心 │ 狀態   │ 分歧                 │
├──────┼────────┼──────┼────────┼──────────────────────┤
│ 風險 │ 偏空   │ 0.62 │ normal │ ⚠️ 與 sentiment 相反 │
│ 情緒 │ 偏多   │ 0.58 │ low_c  │ ⚠️ 與 risk 相反     │
│ 新聞 │ 中性   │ 0.41 │ low_c  │ —                    │
│ 基本 │ —      │ —    │abstain │ —                    │
│ 催化 │ 偏多   │ 0.66 │ normal │ ⚠️ 與 risk 分歧     │
├──────┴────────┴──────┴────────┴──────────────────────┤
│ 綜合：分歧狀態 — 情緒/催化偏正向，但風險面未解除     │
│ 證據獨立性：72%（5 個獨立來源 / 7 總來源）           │
└──────────────────────────────────────────────────────┘
```

**互動**：
- 點擊任一角度行 → 展開 drilldown（複用 AnalysisReportView）
- ConflictBadge 用橙色 pill 標示
- mobile 改用 card layout（每角度一張卡）
- 觸發按鈕：「執行五角度綜合分析」（明確標示會消耗 5× 預算）

**新增 endpoint type**：
```typescript
interface MultiAngleReport {
  coin: string
  snapshot_id: string
  angles: AngleResult[]
  consensus: string
  conflicts: AngleConflict[]
  agreement_matrix: Record<string, Record<string, string>>
  synthesis_summary: string
  evidence_independence: number
  limits: string[]
}
```

**預計工作量**：3-4 小時
**依賴**：PR2

---

### PR4（選填）：Synthesis LLM 敘事

**修改檔案**：`src/trustforge/multi_angle.py`

**內容**：
- 新增 `narrate_synthesis(report: MultiAngleReport, client, log) -> str`
- 把 `conflicts` 列表 + `consensus` + `agreement_matrix` 餵給 Bedrock
- prompt 硬約束：「只能用下列結構化資料敘事，不可自行發明交叉訊號」
- 成本：1 次額外 Bedrock 呼叫（短 prompt），受 budget_guard 控管
- 失敗降級：直接用 `synthesis_summary`（程式組裝的文字）替代

**預計工作量**：2 小時
**依賴**：PR2

---

## 時程建議

| 階段 | PR | 預計 | 里程碑 |
|------|-----|------|--------|
| Phase 1 | PR1 | Day 1 | 純演算法 + 測試通過，可 demo 確定性 synthesis |
| Phase 2 | PR2 | Day 1-2 | 後端完整，API 可呼叫 |
| Phase 3 | PR3 | Day 2-3 | 前端上線，使用者可操作 |
| Phase 4 | PR4 | Day 3（選填） | LLM 敘事加持，報告更好讀 |

---

## 文件修正（P0，在 PR1 之前或同時）

以下文件/UI 若有「五角度交叉綜合評估」字樣，在功能上線前須改為「提供五種分析視角」：
- `frontend/src/hermes/hermesI18n.tsx` — 確認無過度宣稱
- `README.md` — 確認描述準確
- 競賽簡報（如有）

---

## 設計原則

1. **確定性優先**：synthesis 結論由公式/比對產出，LLM 不決策
2. **additive 不破壞**：現有單角度流程零修改，multi-angle 是新功能
3. **成本透明**：前端明確告知「5× 預算消耗」
4. **誠實三態**：任一角度 abstain，綜合不硬拉 normal
5. **溯源完整**：每個結論可追溯到具體角度 → 具體 claim → 具體 evidence → source URL
6. **零第三方依賴**：純 stdlib + boto3，不引入新 package

---

## 裁示建議

> 將「五個分析角度」重新定義為兩層能力：
> - 現有：五種單視角分析（已上線）
> - 新增 P1：五角度綜合評估（本計劃）
>
> 實作時必須共用同一 source snapshot，五角度分別產出 AngleResult，
> 再由 deterministic synthesis 層標示共識、分歧與 abstain；
> LLM 只能負責敘事，不可自行發明交叉訊號。
> 正式完成前，文件與 UI 不得宣稱已支援五角度交叉綜合。
