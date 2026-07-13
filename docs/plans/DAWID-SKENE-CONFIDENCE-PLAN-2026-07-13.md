# Dawid-Skene 信心收斂開發計劃 — 2026-07-13

> 對應報告：`docs/architecture/CONFIDENCE-CONVERGENCE-REPORT-2026-07-13.md`
> 研究依據：`docs/architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md`（#179）
> 話術背景：#178（離線 no-op 定性）
> 排程窗口：決賽前 3 週
>
> **明確排除**：conformal prediction / AUC / 預測力校準相關的任何程式碼、資料、
> 文件（#167、`docs/qa/CONFORMAL-FINDING.md` 範圍），本計劃不涉及。

## 1. 資料結構設計

新模組（不動既有檔案）：`src/trustforge/trust/dawid_skene.py`

```python
# 概念介面（本輪不落地實作，供排程規劃用）
def em_source_reliability(
    votes: dict[tuple[str, str], dict[str, str]],
    # {(coin, time_window_key): {source: direction_label}}
    n_iter: int = ...,
    tol: float = ...,
) -> tuple[dict[str, dict[str, dict[str, float]]], dict[tuple[str, str], dict[str, float]]]:
    """回傳 (confusion_matrix_by_source, posterior_by_item)。

    confusion_matrix_by_source[source][true_label][observed_label] = prob
    posterior_by_item[(coin, window)][label] = posterior_prob
    """
```

- **來源可靠度矩陣**：`dict[source, dict[true_label, dict[observed_label, float]]]`
  （混淆矩陣），標籤集合固定為 `{"bullish", "bearish", "neutral"}`（沿用
  `Claim.direction` 既有類別，不新增新標籤系統）。
- **投票輸入的分組 key**：`(coin, time_window)`，比照 `_iterate_source_reputation`
  既有「同標的」語意，新增「時間窗」維度（避免把跨越數天的舊/新方向判斷混進
  同一組共識，防止 stale claim 稀釋新資訊——時間窗大小沿用既有 `_recency_decay`
  的參數風格，具體數字留待階段 2 依歷史 claim 密度調校，不在本計劃預先定死）。
- **EM 迭代邏輯**放在同一檔案內的私有函式（`_em_step`、`_init_confusion_matrix`
  等），風格比照 `_iterate_source_reputation`：bounded 迭代（硬上限輪數）、
  收斂用似然差 `< tol` 提早停、無隨機性（deterministic 初始化，不用隨機重啟，
  避免測試不可重複——若後續發現對初始化敏感需要多次重啟，重啟種子需固定
  deterministic 序列，不用 `random` 模組不設種子的版本）。
- 產出的來源可靠度需要**轉換成與 `SourceReputation`（0-1 分數）相容的單一
  數值**才能回填進 `_dynamic_reputation` 的 SR 混合公式——轉換規則（如「對角線
  平均正確率」）留待階段 2 實作時定案，本計劃先確認介面點。

## 2. 與現有 `_dynamic_reputation` 的介面整合方式

- **觸發時機**：`_iterate_source_reputation()` 內，當 `stance_fn is None` 時
  （目前直接呼叫 `agree_union_of`/`contra_union_of` 皆為空集合、SR 恆等於
  `sr0`，即 no-op 分支），改為呼叫 Dawid-Skene fallback 路徑：

  ```
  if stance_fn is None:
      # 現況：不計入任何集合，SR 不變（no-op）
      # 改為：呼叫 em_source_reliability() 用 direction 標籤做共識收斂，
      #       回填 SR，而不是原地不動
  ```

  `stance_fn` 存在但個別呼叫回傳 `"neutral"`（fail-safe 降級，無法區分真中立）
  的情況**維持現狀不變**——這一路是「有 client 但這一對判定不明確」，不等於
  「完全沒有分類器」，不觸發 fallback，避免混淆 #178 已定性清楚的兩種情境。
- **不改變既有函式簽名**：`_iterate_source_reputation`/`score()` 的參數、
  回傳型別、`reputation_trace` 結構全部維持向後相容；Dawid-Skene 路徑走完後
  一樣要能填出 `{source, prior, final, agree_n, contradict_n, iterations_run}`
  （`agree_n`/`contradict_n` 在 DS fallback 下語意改為「參與該來源共識投票的
  同標的-時間窗筆數」，需在 trace 文件/docstring 中明確標註兩種模式的欄位語意
  差異，避免決賽問答時把兩種機制的數字混為一談）。
- **W2 既有回歸鎖**（`dynamic_reputation=False` 時完全跳過）不受影響——本計劃
  只動 `dynamic_reputation=True` 且 `stance_fn is None` 這一個分支。

## 3. 測試策略

- **EM 收斂正確性**（新檔 `tests/test_dawid_skene.py`）：
  - 合成資料：已知「真標籤」+ 已知「來源噪聲率」生成的模擬投票，驗證 EM 估出的
    混淆矩陣與真實噪聲率誤差在容忍範圍內（標準 Dawid-Skene 驗證手法）。
  - 收斂性斷言：似然序列單調不減（每輪算 log-likelihood，斷言 `L[t] >= L[t-1] - eps`）。
  - 邊界案例：單一來源（無法估混淆矩陣，需 fallback 回先驗）、全體來源一致投票
    （應立即收斂）、來源數 < 標籤數（小樣本退化情境）。
  - 決定性：同一輸入跑兩次結果逐位元相同（比照 `_iterate_source_reputation`
    對 `PYTHONHASHSEED` 的既有防禦手法，`sorted()` + `math.fsum`）。
- **Fallback 觸發時機正確性**（擴充 `tests/test_trust_scoring.py` 或新增
  `tests/test_w2_dawid_skene_fallback.py`）：
  - `stance_fn is None` 且 `dynamic_reputation=True` → 觸發 DS 路徑，SR 隨
    投票分布變化（不再是恆等 no-op），需新增/更新對應既有
    `test_run_agent_pipeline_dynamic_reputation_offline_is_honest_noop`
    測試的預期行為（no-op 語意會改變，需與 #178 話術同步更新，並在該測試
    docstring 註明「離線不再是 no-op，而是 DS EM 統計收斂」）。
  - `stance_fn` 存在、回傳皆為 `"neutral"` → **不**觸發 DS fallback，維持現狀
    保守排除（回歸測試，鎖住兩種情境的邊界）。
  - `stance_fn` 存在且有 `"entailment"`/`"contradiction"` → 走既有語意路徑，
    DS fallback 完全不介入（互斥分支回歸測試）。

## 4. 分階段任務拆解 + 預估工時

| 階段 | 任務 | 預估工時 | 產出 |
|---|---|---|---|
| 1 | `dawid_skene.py` 骨架 + 合成資料單元測試（不接線） | 1.5 天 | 獨立可測試的 EM 函式庫 |
| 2 | 標籤/時間窗分組邏輯 + 來源可靠度→SR 分數轉換規則定案 | 1 天 | 轉換函式 + 決策記錄（ADR 或本文件更新） |
| 3 | `_iterate_source_reputation` 介面整合（`stance_fn is None` 分支改接 DS） | 1 天 | 修改 `scoring.py`，含 trace 欄位語意更新 |
| 4 | Fallback 觸發時機測試 + 既有 no-op 測試更新 + #178 話術文件同步 | 1 天 | 測試綠燈 + 話術對齊 |
| 5 | 決賽敘事整合（trace 可視化，若排進 #171 分層評分 UI 範圍則跨票協調） | 0.5 天（規劃，不含 UI 實作） | 與 #171 的銜接說明 |

合計約 **5 天**（含測試），可排入決賽前 3 週開發排程前段，為後續 UI/敘事整合
（#171）預留緩衝。

## 5. 明確排除項

- 不處理 conformal prediction 校準、AUC 預測力問題（#167 範圍）。
- 不修改 `src/trustforge/trust/conformal.py`。
- 不變更 `docs/qa/CONFORMAL-FINDING.md` 的既有結論。
- 不在本輪新增任何「預測未來漲跌」相關的評估指標或宣稱；Dawid-Skene 產出的是
  「多來源方向標籤的統計共識信心」，不是「對未來真實方向的預測力」——兩者在
  文件與話術中須明確區分，避免決賽問答時被誤讀成解決了 #167/AUC 問題。
