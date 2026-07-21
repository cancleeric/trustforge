# PLAN: 多模型語意分析架構 (#367 + #368)

> 作者：gray（CPO）
> 日期：2026-07-21
> Issue：#367（校準模型導致棄權）、#368（多模型語意分析架構）
> 優先級：P0-critical

---

## 背景

目前方向判定依賴：
1. OHLCV 14 天報酬率 ±3% 硬門檻（`_price_trend_direction`）
2. stance claims 的 bullish/bearish 關鍵字加權（`_infer_direction` regex）
3. isotonic 校準模型（`data/model-artifacts/calibration-model.json`）

**問題**：校準模型用方向全中性的舊資料訓練 → 學到「所有預測都不準」→ `_calibrate_confidence()` 把 raw 0.5 壓到 0.29 → 永遠觸發 `is_abstain`（門檻 0.50）→ 5 幣全部判定「不明」。

**根治方向**：不再用壞模型，並用 LLM 語意分析取代關鍵字硬編碼，最後以 Dawid-Skene 信譽加權合併多模型判斷。

---

## Phase 1（緊急，今天 7/21）：移除壞校準模型 + 棄權時仍給方向

### 目標
- 立即解除 5 幣全棄權問題
- 棄權時仍顯示方向趨勢（降級參考）

### 具體任務

| # | 任務 | 檔案 | 說明 |
|---|------|------|------|
| 1.1 | 移除壞校準模型 | `data/model-artifacts/calibration-model.json` | `git rm`，讓 `_calibrate_confidence()` fallback 到 `_CALIBRATION_TABLE` 硬編碼 |
| 1.2 | 修改棄權邏輯 | `src/trustforge/agent/orchestrator.py` ~L1095-1110 | `is_abstain=True` 時仍呼叫 `_direction(brief.supporting, all_scored=scored)` |
| 1.3 | 修改棄權文案 | 同上 | `head = f"{coin}：資料不足以做確信判斷，但價格趨勢指向{direction}（僅供參考，非投資建議）。"` |
| 1.4 | decision_state 更新 | 同上 | 棄權時 `direction` 不再硬設 `"不明"` |
| 1.5 | 驗證回歸 | `tests/` | 確認既有 13 測試通過 |
| 1.6 | 親測 5 幣 | CLI | `python -m trustforge.cli analyze --coin {BTC,ETH,SOL,BNB,XRP} --offline` |

### 修改細節

```python
# orchestrator.py build_report 棄權段（修改後）
if is_abstain:
    # 棄權 = 信心低，但方向仍可標記供參考
    direction = _direction(brief.supporting, all_scored=scored)
    if direction == "不明":
        head = (
            f"{coin}：現有資料不足以判斷市場方向"
            f"（支撐證據 {n_supporting} 筆、校準後資訊完整度 {calibrated:.2f}），"
            "暫不給出方向性結論，建議待更多獨立來源佐證後再評估。"
        )
    else:
        head = (
            f"{coin}：資料不足以做確信判斷，"
            f"但價格趨勢指向{direction}（僅供參考，非投資建議）。"
        )
```

### 驗收標準（可量化）

- [ ] `data/model-artifacts/calibration-model.json` 已從 git 移除
- [ ] `_calibrate_confidence()` fallback 到硬編碼查表，raw 0.5 映射後 ≥ 0.45
- [ ] 5 幣離線分析中，≥4 幣 direction ≠ "不明"
- [ ] `pytest -q` 全數通過（0 failure）
- [ ] `out/<coin>/report.md` 含方向關鍵字（偏多/偏空/中性）

### 檢核條件

- 移除模型後 `_load_cached_calibration_model()` 回傳 `None` → 走 fallback
- `_CALIBRATION_TABLE` 的映射合理（raw 0.5 → calibrated ≥ 0.45）
- 棄權邏輯仍保留（信心不足的警告不移除，只補方向）

### 異常處理

| 情境 | 處理 |
|------|------|
| fallback 查表後仍 < 0.50 | 檢查 `_CALIBRATION_TABLE` 定義，必要時暫時調低 `_ABSTAIN_CALIBRATED_THRESHOLD` 到 0.30 |
| `_direction()` 在棄權路徑回傳 "不明" | 允許；文案用「資料不足以判斷」不含方向 |
| 測試因 calibrated 值變動而失敗 | 更新 test assertions 到新映射值 |

---

## Phase 2（本週 7/22–7/25）：LLM 語意方向分析

### 目標
- 用 Bedrock LLM 做語意方向判斷，取代 `_infer_direction()` 的 regex 路徑
- 每個來源類型有專屬 prompt，輸出結構化結果
- 用 Converse API `tool_use` 強制結構化輸出

### 具體任務

| # | 任務 | 檔案 | 說明 |
|---|------|------|------|
| 2.1 | 設計 prompt 模板 | `src/trustforge/trust/semantic_prompts.py`（新建） | 3 組 prompt：price / news / onchain |
| 2.2 | 建立語意分析器 | `src/trustforge/trust/semantic_analyzer.py`（新建） | 呼叫 Bedrock Converse API，強制 tool_use 結構化 |
| 2.3 | 定義輸出 schema | 同上 | `{direction: bullish|bearish|neutral, confidence: 0-1, reasoning: str}` |
| 2.4 | 價格 prompt | `semantic_prompts.py` | 「根據以下 OHLCV 數據，判斷短期趨勢方向」 |
| 2.5 | 新聞 prompt | 同上 | 「根據以下新聞摘要，判斷市場情緒方向」 |
| 2.6 | 鏈上 prompt | 同上 | 「根據以下鏈上指標，判斷資金流向與市場信號」 |
| 2.7 | 整合到 `_direction()` | `orchestrator.py` | 線上模式用語意分析器；離線 fallback 到 Layer 1/2 |
| 2.8 | 時間預算控管 | `semantic_analyzer.py` | 單次呼叫 timeout + 總時間檢查（`STANCE_TIME_RESERVE_SEC` 對齊） |
| 2.9 | 快取機制 | 同上 | 同源同文 24h 快取（SQLite），避免重複呼叫 |
| 2.10 | 測試 | `tests/test_semantic_analyzer.py`（新建） | mock Bedrock 回應測 parse + 快取 + timeout |

### Prompt 設計原則

```python
# Converse API tool_use schema（強制結構化輸出）
DIRECTION_TOOL = {
    "name": "report_direction",
    "description": "報告分析結果",
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "maxLength": 200}
        },
        "required": ["direction", "confidence", "reasoning"]
    }
}
```

### 各來源 prompt 模板結構

```
[System] 你是加密市場分析專家。根據提供的{來源類型}資料，判斷{幣種}的短期方向。
[Rules] 1. 只根據提供的資料判斷，不使用訓練知識
        2. confidence 反映資料充分程度，非你對市場的個人看法
        3. 模稜兩可時回 neutral + 低 confidence
[User]  {structured_data}
[Tool]  report_direction（強制呼叫）
```

### 驗收標準（可量化）

- [ ] 3 組 prompt（price/news/onchain）各通過 mock 測試
- [ ] Converse API tool_use 回應正確 parse 成 `{direction, confidence, reasoning}`
- [ ] parse 失敗時 graceful fallback 到 regex 路徑（不崩）
- [ ] 單次語意分析 ≤ 8 秒（read timeout）
- [ ] 快取命中率驗證：同輸入第二次不呼叫 Bedrock
- [ ] 線上模式 5 幣分析，≥4 幣有語意方向輸出
- [ ] 離線模式不呼叫 Bedrock，fallback 到 Layer 1/2

### 檢核條件

- 所有 Bedrock 呼叫經 `src/trustforge/bedrock.py`（競賽硬約束）
- 新增呼叫納入 `execlog` 成本記帳
- 時間預算：語意分析 ≤ 3 次呼叫 × 8s = 24s；留 `STANCE_TIME_RESERVE_SEC`(25s) 給後續
- 不違反反作弊鐵則：LLM 只做「分類判斷」，不取代 pipeline 的證據整合

### 異常處理

| 情境 | 處理 |
|------|------|
| Bedrock timeout / throttle | fallback 到 `_infer_direction()`（regex），log 降級事件 |
| tool_use 回應格式異常 | parse error → fallback regex，不 crash |
| confidence 全部 < 0.3 | 視為 neutral，不覆蓋 Layer 1 價格事實 |
| 15 分鐘預算不足 | 跳過語意分析，直接用 Layer 1/2 |

---

## Phase 3（下週 7/28–8/1）：多模型加權合併

### 目標
- 各模型/來源獨立判斷，以 Dawid-Skene 信譽加權合併
- 最終方向 = 加權多數決
- confidence = 合併後的一致性程度

### 具體任務

| # | 任務 | 檔案 | 說明 |
|---|------|------|------|
| 3.1 | 多模型投票結構 | `src/trustforge/trust/multi_model_voter.py`（新建） | 收集多源方向判斷的投票器 |
| 3.2 | 確定性 OHLCV 判斷器 | 同上 | 現有 `_price_trend_direction` 抽象為獨立 voter |
| 3.3 | LLM-price voter | 同上 | Phase 2 的 price prompt |
| 3.4 | LLM-news voter | 同上 | Phase 2 的 news prompt |
| 3.5 | LLM-onchain voter | 同上 | Phase 2 的 onchain prompt |
| 3.6 | Dawid-Skene 加權 | 同上 | 把各 voter 的 {direction, confidence} 餵入 `dawid_skene.em_source_reliability` |
| 3.7 | 加權多數決 | 同上 | `final_direction = argmax(Σ r(voter) × vote_for_label)` |
| 3.8 | 一致性 confidence | 同上 | `confidence = max_label_weight / total_weight`（歸一化後的最大佔比） |
| 3.9 | 替換 `_direction()` | `orchestrator.py` | 改用 `multi_model_voter.aggregate_direction()` |
| 3.10 | 降級策略 | 同上 | 可用 voter < 2 時 fallback 到單一 Layer 1 |
| 3.11 | 測試 | `tests/test_multi_model_voter.py`（新建） | 模擬多 voter 場景：一致/分歧/部分失敗 |
| 3.12 | 整合測試 | `tests/test_pipeline_direction.py`（新建） | 5 幣端到端驗證 |

### 架構圖

```
多源資料
  ├→ Voter A: OHLCV 統計（確定性，r=0.95）    → {direction, confidence}
  ├→ Voter B: LLM-price（Bedrock, r=dynamic）  → {direction, confidence}
  ├→ Voter C: LLM-news（Bedrock, r=dynamic）   → {direction, confidence}
  ├→ Voter D: LLM-onchain（Bedrock, r=dynamic）→ {direction, confidence}
  └→ Voter E: stance consensus（regex backup）  → {direction, confidence}
       │
       ▼
  Dawid-Skene EM → r(voter) 動態信譽
       │
       ▼
  加權多數決 → final_direction + calibrated_confidence
```

### 加權合併公式

```
score[label] = Σ_i ( r(voter_i) × confidence_i × I[vote_i == label] )
final_direction = argmax(score[label])
final_confidence = score[final_direction] / Σ score[all_labels]
```

其中：
- `r(voter_i)`：Dawid-Skene EM 估出的 voter 信譽（初始 = 來源基礎信譽）
- `confidence_i`：該 voter 自己回報的信心
- `I[vote_i == label]`：indicator function

### Dawid-Skene 整合

利用現有 `trust/dawid_skene.py` 的 `em_source_reliability()`：
- items = 各幣種的方向判斷任務
- raters = 各 voter（OHLCV / LLM-price / LLM-news / LLM-onchain / stance）
- labels = `("bullish", "bearish", "neutral")`
- 累積歷史判斷結果（SQLite），EM 收斂後取 `r(source)` 作為加權

### 驗收標準（可量化）

- [ ] 5 幣分析全部有 direction 輸出（0 個「不明」在正常模式）
- [ ] 多 voter 一致時 confidence ≥ 0.70
- [ ] 多 voter 分歧時 confidence ≤ 0.50 且 direction 不亂跳
- [ ] 部分 voter 失敗（timeout）時仍有結果輸出（降級不崩）
- [ ] Dawid-Skene 迭代 ≤ 20 輪收斂（不吃時間預算）
- [ ] 整體 pipeline 15 分鐘內完成
- [ ] 測試覆蓋：一致場景 / 分歧場景 / 降級場景 / 單 voter 場景

### 檢核條件

- OHLCV voter 為最高優先 fallback（永遠可用，不依賴 Bedrock）
- Dawid-Skene `_reputation_floor` 防止信譽蒸發到 0
- 加權合併仍為確定性公式（不交 LLM 黑箱）→ 符合信任層鐵則
- 新增 voter 不影響現有 `_price_trend_direction` 的客觀事實優先邏輯

### 異常處理

| 情境 | 處理 |
|------|------|
| 所有 LLM voter timeout | fallback 到 Voter A（OHLCV）+ Voter E（stance regex） |
| Dawid-Skene 不收斂 | 硬限 20 輪後取當前估計值；log 警告 |
| voter 數 < 3（DS 退化） | 所有 voter `r=0.5`（等權），走簡單多數決 |
| 某 voter 持續與多數對立 | DS 自動降低其 r；不手動干預 |
| confidence 全部很低 | 回 neutral + low_confidence 標記，不強給方向 |

---

## 跨 Phase 約束

### 時間預算分配（15 分鐘總預算）

| 階段 | 最大耗時 | 說明 |
|------|----------|------|
| 多源抓取 | 60s | 平行抓取 + timeout 5s/源 |
| claim 抽取 | 30s | Bedrock 1 次 |
| stance 配對 | 40×8s=320s (5.3min) | 有預算限制 `DEFAULT_STANCE_PAIR_BUDGET` |
| **語意方向分析（Phase 2）** | **3×8s=24s** | 3 prompt × 8s timeout |
| **多模型合併（Phase 3）** | **<1s** | 純計算 |
| 報告生成 | 30s | Bedrock 行文 |
| 收尾 | 15s | 寫檔/log |
| **STANCE_TIME_RESERVE** | **25s** | 安全裕量 |
| **總計** | **≤ 8 分鐘** | 遠低於 15 分鐘 ✅ |

### 反作弊合規

- LLM 語意分析 = 「分類工具」（判方向），不是「產出結論」
- 最終信任分由確定性公式算出（加權多數決 + DS 信譽）
- Bedrock 只負責「行文」+「分類」，不代替 pipeline 做判斷
- 每個結論帶溯源鏈：voter 投票 → 加權合併 → 最終方向

### 相容性

- Phase 1 即可獨立上線（hotfix 性質）
- Phase 2 加入語意分析器但預設 disabled（feature flag `SEMANTIC_DIRECTION_ENABLED`）
- Phase 3 整合後移除 feature flag，成為預設路徑
- 離線模式（`--offline`）始終走 Layer 1/2（不呼叫 Bedrock）

---

## 學術方法對齊

| 方法 | 實作 | 文獻 |
|------|------|------|
| Dawid-Skene 動態信譽 | `trust/dawid_skene.py` | Dawid & Skene (1979) |
| 加權多數決 | `multi_model_voter.py` Phase 3 | Condorcet's Jury Theorem |
| 校準 | `_CALIBRATION_TABLE` fallback | Guo et al. (2017) ICML |
| 多源衝突聚合 | DS EM + 加權合併 | Li et al. (2014) CATD |

---

## 里程碑

| 日期 | Phase | 交付 |
|------|-------|------|
| 7/21（今天） | Phase 1 完成 | 5 幣有方向，壞模型移除 |
| 7/25（週五） | Phase 2 完成 | 語意分析可用（feature flag on） |
| 7/31（下週四） | Phase 3 完成 | 多模型加權上線 |
| 8/1（競賽日） | 全路徑驗證 | Live Demo 展示語意+加權方向 |

---

## 風險

| 風險 | 機率 | 影響 | 緩解 |
|------|------|------|------|
| Phase 1 fallback 查表仍低於門檻 | 低 | 高 | 同步降低 `_ABSTAIN_CALIBRATED_THRESHOLD` |
| Bedrock throttle 在語意分析 | 中 | 中 | regex fallback + 快取 |
| DS 在 voter 少時退化 | 中 | 低 | 等權 fallback |
| 15 分鐘超時 | 低 | 高 | 語意分析有獨立 timeout guard |
| 競賽日 Bedrock 不穩 | 中 | 高 | 離線模式 + OHLCV voter 永遠可用 |
