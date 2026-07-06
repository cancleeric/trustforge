# W2 truth-discovery 動態來源信譽 — 最終啟用計劃

> 作者：gray（CPO）｜日期：2026-07-03
> 背景：`docs/archive/plans/PLAN-w2-wiring.md`（2026-07-01 舊接線計劃，含前提驗證數據）已審完
> 10 天未執行。本文件是**最終可執行啟用計劃**——重新 grep 實證現況（含 07-01
> 之後兩天的新增變更：資料密度批次 #54/#55、deploy gate 修正），確認啟用前置
> 硬化是否真的待辦，並給出一步到位的 CTO 執行範圍。**未改任何程式碼。**

## ① W2 現況（grep 實證，2026-07-03 重新核對）

- 引擎：`src/trustforge/trust/scoring.py`，PR #29 已合併進 main（commit
  `ba099aa feat(w2): truth-discovery 動態來源信譽基礎, default-OFF`）。
- `score()` 簽名 `scoring.py:1116`：`dynamic_reputation: bool = False`（預設關）。
- 生產唯一呼叫點 `src/trustforge/agent/orchestrator.py:778`：
  `score(claims, now=now_ts, stance_fn=shared_stance_fn)` —— **grep 確認沒有
  `dynamic_reputation=`**，逐字複核 `docs/archive/plans/PLAN-w2-wiring.md` 07-01 的發現，兩天
  來零變化：**W2 100% 未接線，仍是唯一差距**。
- 為何預設關：純粹「方案審完未排執行」，不是引擎有已知缺陷卡著（見下方 #1 發現）。

## ② 是否 $0 / 確定性

**是確定性演算法，不需額外呼叫 LLM/Bedrock，啟用邊際成本 $0。** 證據：
`_iterate_source_reputation` 的 K 輪迭代只重算數學混合權重（`_stable_sigmoid`
加權平均），佐證/矛盾來源集合由 `_reputation_evidence()` 只算一次、與既有
`_corroboration()` 共用同一個 `cached_stance_fn` 記憶體快取——K 輪迭代**不會
多打一次 Bedrock**。`tests/test_stance_budget_sharing.py` 6/6 測試綠證實同一
`_StanceBudget` 實例被 `score()` 與跨源 stance_pairs 偵測共用，總呼叫上限不變。

## ③ 前置硬化 #1／#9 — grep 沒找到，但實質已做完（重要發現）

`grep -rn "per-source stance 聚合\|單源灌爆\|Tier2 online-stance\|online-stance 預算配額"`
遍歷全 repo（含 `docs/`、`ROADMAP.md`、`OPTIMIZATION-PLAN-weakness.md`、
`WORLD-FIRST-MASTER-PLAN.md`）**零命中**——找不到字面的「task #1／#9」條目，
無法確認這兩個編號出自哪份追蹤清單。但**逐字核對其描述的問題本身**，發現已在
PR #29 的 4 輪 codex 對抗審中修完並有回歸測試鎖住：

| 風險 | 修法 | 程式碼位置 | 測試（今日重跑，全綠） |
|---|---|---|---|
| 同源重複貼已佐證 claim 放大票數 | `agree_union_of`/`contra_union_of` 先聯集去重再計票（HIGH-1） | `scoring.py:982-997` | `test_duplicate_corroborated_claim_does_not_inflate_reputation`（+ public API 版） |
| 重貼自己最高分 claim 拉高投票權重 | `unique_claims_by_source` 按文本去重才取平均（第2輪HIGH） | `scoring.py:1012-1021` | 同上兩條 |
| 單源灌水 30 篇自我佐證 | 需「其他」獨立來源才算 agreement，小樣本守門 <3 強制 α=1 | `scoring.py:999-1003` | `test_anti_spam_single_source_cannot_inflate_own_reputation` |
| 500 源同時判矛盾導致 OverflowError/信譽歸零 | `_stable_sigmoid` clamp±30 + `_reputation_floor` | `scoring.py:851-863,76-83` | `test_large_scale_contradiction_score_does_not_crash_bounded` |
| stance 呼叫預算被 W2 疊加吃兩倍 | `score()`/跨源偵測共用同一 `_StanceBudget` | `orchestrator.py:774` | `test_shared_stance_budget_caps_total_calls_...` |

今日重跑 `pytest tests/test_trust_scoring.py -k "reputation or anti_spam or
duplicate_corroborated"` → **15/15 passed**；`test_stance_budget_sharing.py`
→ **6/6 passed**。

**結論：沒有找到需要新 PR 才能補的前置硬化缺口。** 建議：CEO/CTO 若手上有
獨立於本 repo 文件的任務追蹤系統列著 #1/#9，請對照上表逐條核對是否已被
PR #29 涵蓋；若追蹤系統條目與上表描述一致，可直接標記完成，不需另開工。

## ④ 啟用會改什麼（誠實，07-01 舊數據 + 建議重測）

07-01 worktree 實測（`PLAN-w2-wiring.md`①）：BTC 17 claims 中 2 個來源變動
（`coindesk` 0.65→0.75、`x-trader-z` 0.35→0.588，被 3 源同時佐證同一賣壓事件），
confidence 0.6125→0.6279；ETH/SOL 因獨立佐證聯集 <3（小樣本守門）**全數不變**。
**07-01 之後兩天新增了 6 家新聞 + 3 個鏈上源**（commit `a372629`/`e262e75`），
樣本池已變大，上述具體數字很可能過期——啟用前必須用當前 main 重跑一次
before/after，不能沿用舊數字當最終驗收證據。方向不變：權威源（onchain/
regulatory）被多源佐證會升，被矛盾壓制的源會降（floor 保底 30% 基礎信譽，
不會蒸發到 0）；影響幅度受測試鎖定在 ±0.15、不翻倍不歸零。

## ⑤ 啟用方式：預設開，不做 feature flag

單行改動 `orchestrator.py:778` 加 `dynamic_reputation=True`。理由：小樣本守門
本身就是失效安全（資料不足=純先驗，行為等同關閉），不存在需要開關保護的失控
風險；暴露成 CLI/web flag 只會增加範圍，黑客松/生產都不需要。

**Rollout 步驟**：
1. 獨立 worktree 用當前 main + 最新資料源重跑 BTC/ETH/SOL before/after（更新
   ①的數字，非沿用 07-01 舊表）。
2. 補可解釋性：`orchestrator._scored_to_evidence`（現況 grep 確認**未讀
   `sc.reputation_trace`**，只取 `sc.components`）加 `reputation_prior/final/
   agree_n/contradict_n` 到 `trust_components`；`build_report` 的 explanation
   附一句「動態信譽：0.35→0.59，經 3 源互證」。
3. 全測試套件跑一次（現況 950+ 全綠）確認零回歸。
4. 合併，EC2 部署，跑一次線上真三幣 before/after 對照存證。

## ⑥ 驗收（CEO 親測）

沿用 `PLAN-w2-wiring.md`③既有 4 條標準（BTC 可見變化＋解釋文字、ETH/SOL 無
變化但 trace 可查、三幣對照表、操縱來源信譽下降單元測試）＋本計劃新增：
5. 確認 `stress_test.py`／pytest 前後 Bedrock 呼叫次數不變（佐證 $0）。
6. #24 檢查：不得為了讓 ETH/SOL「看起來有變化」調整
   `MIN_INDEPENDENT_EVIDENCE` 或造資料。

## ⑦ CTO 執行範圍（單一 PR，不必分兩批）

因 #1/#9 實質已做完，不需要「先硬化 PR、再啟用 PR」兩階段。單一 PR：
`orchestrator.py` 一行 `dynamic_reputation=True` + `_scored_to_evidence`/
`build_report` 可解釋性擴充（純 dict/str 擴充，不動 schema）+ 重新產出的
三幣 before/after 表 + 全測試套件跑一次。預估 <15 行程式碼異動。

## ⑧ 風險

- **敘事風險**：既有 demo 截圖若展示 BTC 分數，啟用後同query分數可能有
  ±0.15 內差異，簡報需加註「動態信譽已啟用」。
- **看起來沒變化的誤判**：ETH/SOL 多數 query 因小樣本守門不變，需靠②的
  explanation 主動說明，否則評審可能誤判為接線失敗（07-01 已預見此風險）。
- **舊數字過期**：資料密度增加後具體數字需重測，不可用 07-01 表格做最終證據。
