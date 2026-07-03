# W2 接線計劃 — truth-discovery 動態來源信譽上線

> 作者：gray（CPO）｜ 日期：2026-07-01
> 背景：`docs/WORLD-FIRST-ANALYSIS.md` §5.5/Tier1 roadmap；W2 引擎已在分支
> `feat/w2-dynamic-reputation`（PR #29，4 輪 codex 對抗審，284 測試綠）交付，
> 但**未合併進 main、也未在任何呼叫點啟用**。CEO 親測發現：
> `score()` 的 `dynamic_reputation` 預設 `False`；生產路徑
> `src/trustforge/agent/orchestrator.py:363` 呼叫 `score(...)` **沒有**帶
> `dynamic_reputation=True`；grep 全 `src/` 無任何啟用點 → W2 在真實
> demo/CLI/web/EC2 上永遠不執行。
>
> 本文件**只做計劃驗證與設計**，未修改任何程式碼/測試，未合併/checkout 任何分支
> 到共用工作目錄（實證分析在獨立 `git worktree` 中進行並已清除）。

---

## ① 前提驗證結論（實證數據）

**方法**：在獨立 worktree checkout `feat/w2-dynamic-reputation`（PR #29 內容），
用官方離線路徑 `collect(coin, offline=True)` 取 `demo/sample_data/` + `data/data`
真實 BTC/ETH/SOL 樣本，直接呼叫 `extract_claims()` + `score(..., dynamic_reputation=True/False)`
逐 claim / 逐 source 比對，不造任何資料。

**結論：不是「引擎失效」，是兩層問題疊加——(a) 100% 未接線；(b) 接線後對
ETH/SOL 官方樣本規模而言，多數來源仍不觸發，這是設計上的小樣本守門，不是 bug。**

| 幣 | claims/來源數 | score 有差異的 claim | confidence off→on |
|---|---|---|---|
| BTC | 17 claims / 13 sources | 2/17 | 0.6125 → **0.6279** |
| ETH | 14 claims / 12 sources | 0/14 | 0.5956 → 0.5956（無變化） |
| SOL | 9 claims / 6 sources | 0/9 | 0.6125 → 0.6125（無變化） |

逐來源看（`reputation_trace`）：BTC 13 個來源中只有 2 個
（`coindesk` 0.65→0.75、`x-trader-z` 0.35→**0.588**，被 3 個獨立來源同時佐證同一則
「BTC 大額轉入交易所→賣壓」事件）跨過 `MIN_INDEPENDENT_EVIDENCE=3`
（獨立佐證+矛盾來源聯集需 ≥3 個不同來源，否則強制 `alpha=1`＝純先驗、W2 不調整，
這是 PR #29 的「小樣本守門」防呆設計，CEO refinement #1）。ETH/SOL 樣本中**沒有
任何來源**的 agree/contradict 聯集達到 3——因為官方離線樣本每個來源多半只有
1–2 則獨立主張，沒有大新聞事件把多來源同時匯聚到同一主張上。

**釐清三個假設**：
1. **小樣本守門是主因**（非「direction 多為 neutral」）：BTC/ETH/SOL 的 direction
   分佈都相似（neutral 佔六成以上），但 BTC 因為有一則「交易所流入賣壓」事件被
   news+social+onchain 類同時提及才觸發；ETH/SOL 沒有等量級的跨來源同主題事件。
2. **不是「SR 迭代後回到 prior」的算法缺陷**：BTC 兩個觸發來源的位移（+0.10、
   +0.24）幅度合理且方向正確（被多方佐證的來源信譽上升），迭代 2–5 輪內收斂
   （`REPUTATION_CONVERGENCE_EPS=0.01`），非退化成 no-op。
3. **ETH/SOL「不觸發」不是 #24 式「跨源背離框」翻版**：#24 的問題是為了讓功能
   「看起來有東西」而暗中放寬/造資料；這裡的判斷是——**不建議為了讓 ETH/SOL
   在 demo 上「有變化」而去改樣本資料或調低 `MIN_INDEPENDENT_EVIDENCE`**。維持
   現狀 = 誠實：W2 對「單一來源零星爆料」不出手，只有真正被多來源交叉證實/推翻
   的來源信譽才會被調整——這正是 truth-discovery 演算法「該有的行為」，不是缺陷。

**對接線的意義**：W2 接上後，BTC demo 會有**看得到、可解釋、幅度合理**的變化；
ETH/SOL demo 在目前樣本規模下大機率不變。這件事必須**在驗收前先跟 CEO 說清楚**，
避免「親測 ETH 沒變化」被誤判為接線失敗。

---

## ② 接線方案

**唯一呼叫點**：`src/trustforge/agent/orchestrator.py:363`
（grep 全 `src/` 確認 `score(` 只有這一處，無其他重複邏輯需要改）。

```python
scored = score(
    claims,
    now=now_ts,
    stance_client=None if client.offline else client,
    stance_remaining_time_fn=log.remaining,
    dynamic_reputation=True,   # 新增：W2 上線
)
```

- **建議永遠開（不做 config/feature flag）**，理由：
  1. W2 內建「小樣本強制 alpha=1」防呆——資料不足時數學上等同關閉，**不會產生
     錯誤或不穩定行為**，只是「有機會時才調整」，沒有需要開關保護的失控風險。
  2. `reputation_iterations` 用預設 `DEFAULT_REPUTATION_ITERATIONS=3`（硬上限 5，
     `REPUTATION_CONVERGENCE_EPS=0.01` 提前收斂），不需暴露成 CLI/web 參數，避免
     黑客松範圍膨脹。
  3. 離線／線上一致：`_iterate_source_reputation` 共用同一份 `_reputation_evidence`
     （`stance_fn` 只算一次），**開 W2 不會多打一次 Bedrock stance 呼叫**——已用
     worktree 實測確認 `_corroboration()`（既有分項）與 `_reputation_evidence()`
     （W2 新增）共用同一個 memoized `cached_stance_fn`，第二次呼叫是同進程記憶體
     快取命中，非重複真呼叫。**不影響 15 分鐘執行預算、不增加 Bedrock 成本**。
- **離線 vs 線上行為差異**：兩者差異只在於 `stance_fn` 本身的來源
  （線上：cache-miss 才真打 Bedrock Haiku；離線：`cached_stance_fn(None)` 只讀
  `demo/sample_data/stance_cache.json`，miss 則 fail-safe 回 neutral）——這是 W1.5
  既有行為，W2 開關**不改變**這層，只改變「同一份 agree/contradict 證據」如何
  回饋進 SourceReputation。

**可解釋性補強（同一 PR 內，範圍小）**：目前 `ScoredClaim.reputation_trace` 算出
來後**沒有傳到 `Report`/`Evidence`**（`orchestrator._scored_to_evidence` 只取
`sc.components`，未取 `sc.reputation_trace`）。若不補這段，CEO/評審在 UI 上看不到
「為何調整」，等於白做可解釋性。建議最小改法（不改 schema 結構）：
1. `_scored_to_evidence`：`trust_components` dict 裡多塞
   `reputation_prior` / `reputation_final` / `reputation_agree_n` /
   `reputation_contradict_n`（有 `reputation_trace` 時才加，向下相容）。
2. `build_report` 組 `key_basis` 的 `explanation` 字串，若該來源
   `reputation_trace` 存在且 `final != prior`，追加一句
   「（動態信譽：0.35→0.59，經 3 個獨立來源互證）」。
- 兩處都是既有 `dict`/`str` 欄位擴充，**不需要改 `Evidence`/`BasisItem` dataclass
  結構**，前端/測試對舊欄位的既有讀取不受影響。

---

## ③ 驗收標準（CEO 親測清單，看真三幣輸出，不看測試字串）

跑 `python3 -m trustforge.cli`（或 web `/analyze`）離線模式，BTC/ETH/SOL 各一次，
比對接線前後：

1. **BTC 必須有可見變化**：
   - `x-trader-z` 這條來源在 evidence/report 的 `reputation_final`（或 explanation
     字串）從先驗 0.35 明顯上調（預期 ≈0.59），且該來源對應 claim 的 `trust`
     從 0.54 附近升到 0.66 附近。
   - 整體 `confidence` 從 0.6125 升到 0.6279 附近（±0.005 容忍）。
   - `report`/UI 上能看到該來源「為何調整」的一句話解釋（見②-2），非黑箱數字。
2. **ETH/SOL 允許無變化，但必須有「為何沒變化」的可解釋依據**：
   - 至少能在 log/trace 層級查到每個來源的 `agree_n + contradict_n < 3`（即
     `iterations_run` 執行了、`final == prior`），證明 W2 **有跑、只是沒有觸發
     條件**，而不是沒接上。這條是防止「看起來像 bug」的誤判。
3. **三幣前後對照表**（PR 說明附上，非口頭）：至少列
   `幣｜來源｜prior SR｜final SR｜agree_n｜contradict_n｜claim trust off/on｜
   confidence off/on`，BTC 兩列有變化、ETH/SOL 全列 final==prior。
4. **操縱/孤立來源方向驗證**（需額外造一組「來源被多方矛盾」的離線 fixture 測試，
   非改官方 demo 樣本）：至少一條單元測試證明「被 ≥3 獨立來源判 contradiction」
   的來源信譽會**低於**先驗值，不只驗證「被佐證會升」這一半。
5. **15 分鐘/成本無回歸**：跑一次線上 stress（若額度允許）或至少離線
   `scripts/stress_test.py`，確認開 W2 後總耗時、Bedrock 呼叫次數與接線前相同
   （因為 stance_fn 共用快取，理論上應該完全相同）。

---

## ④ 風險與回歸防護

| 風險 | 影響面 | 防護 |
|---|---|---|
| 既有測試對 `report.confidence`/`trust` 有硬編值斷言 | 已 grep `tests/*.py`：目前**無**任何測試對 confidence/trust 做精確數值 `==` 斷言（僅有 `0<=x<=1` 範圍檢查），BTC/ETH/SOL 相關測試無精確值鎖定 | 合併 PR #29 到 main 後，wiring PR 送測前**重跑全量 `pytest -q`**，人工複查任何新增失敗是否為「數值變動」而非邏輯錯誤 |
| 已發布 EC2 demo/screenshot（`docs/AWS-ARCHITECTURE.md` 等）標註的舊 confidence 數字 | 文件/簡報用語若引用了具體舊數字，接線後會不一致 | CTO 接線 PR 完成、CEO 親測通過後，**同一輪**檢查 `docs/` 內是否有寫死的 demo 輸出數字，若有另開小 PR 更新（不混進 wiring PR） |
| `reputation_trace` 未傳到 Evidence/Report 就先開關 | 開了但「不可解釋」，違反 W2/roadmap 的可解釋性訴求，評審看不到「為何調整」 | 已在②方案中把「補可解釋性」與「開關」綁進**同一個 PR**，不可分開驗收 |
| 之後有人為了讓 ETH/SOL「看起來有效果」而調低 `MIN_INDEPENDENT_EVIDENCE` 或加樣本資料 | 重演 #24 式造資料紅線 | 本計劃明文：**本輪不動 `MIN_INDEPENDENT_EVIDENCE`、不加/改 `demo/sample_data`**；若未來要讓 W2 對更多幣種可見，只能靠「真實擴大企業資料規模」（呼應 hoyabit 真連接器），列入 backlog，不在本次範圍 |
| 開關後 stance_fn 被呼叫兩次的效能/成本疑慮 | 已實測確認共用 memoized cache，非重複真呼叫；仍建議 CTO 在 PR 內附一次 `stress_test.py` 前後對照數字佐證，非只憑程式碼推論 | 見驗收標準第 5 條 |

---

## ⑤ 範圍 / PR 切分建議

建議拆成 **兩個** PR，順序執行、各自過 CEO 親測：

1. **PR-A：merge `feat/w2-dynamic-reputation`（PR #29）進 main**
   - 純引擎交付，`dynamic_reputation` 預設仍 `False`，對現有行為零影響（回歸鎖，
     已由 284 測試 + 4 輪 codex 對抗審驗證），風險最低，可獨立先收。
2. **PR-B：接線 + 可解釋性補強（本文件②）**
   - `orchestrator.py:363` 加 `dynamic_reputation=True`。
   - `_scored_to_evidence` + `build_report` 的 explanation 補強（②-2 兩處）。
   - 新增 1 條「操縱來源信譽下降」的單元測試（驗收標準第 4 條）。
   - 附三幣前後對照表（驗收標準第 3 條）於 PR 說明。
   - **不**碰 `MIN_INDEPENDENT_EVIDENCE`、不碰 `demo/sample_data`。

兩個 PR 都在一個 PR 能收的量級內（PR-A 已是獨立完整交付；PR-B 預估變更
`orchestrator.py` ~10 行 + `schema` 說明性 dict 擴充 + 1 條新測試，不需要動
`scoring.py` 核心邏輯）。**不需要**「demo 樣本使 W2 可見」這一步——依①的結論，
刻意讓 ETH/SOL 觸發等同造資料，紅線不做；BTC 現有樣本已足以證明 W2 有效可見。
