# TrustForge — 世界第一 Master 開發計劃（v3 精簡權威版）

> CPO gray｜v3：2026-07-03｜LIVE：main `69e9cc5`/tag `v0.5.13`；生產
> commit `5dcb64b`（含W2啟用+資料密度，差1個no-op commit；`/status`
> `git describe`誤植舊tag基準`v0.3.0-37-g...`，純標籤bug非落後）。
> 證據：`pytest -q`本輪重跑**1127 passed/6 skipped/0 failed**、`curl`
> 生產200、逐檔grep。本文件為總綱，細節見各`docs/PLAN-*.md`。

---

## 最終標準宣言

世界第一＝**多護城核心疊起來**，非單一分數漂亮；商業級只是起點。黑客松
＝全台最強駭客併命賽，不世界第一贏不了。**題目只是方向，別被框死**。
一律誠實分三態標記：能做沒做／做了但簡化／資料卡真做不到。

---

## A. LIVE 現況表（grep/git/curl 實證）

| 項目 | 狀態 | 證據 |
|---|---|---|
| **W2動態來源信譽** | ✅已啟用（entailment-only，$0，離線no-op） | `orchestrator.py:807 dynamic_reputation=True`；<3源守門α=1；`reputation_trace`可解釋 |
| 資料密度 | ✅21個Source（news11+onchain5+coingecko3+reddit1+SEC1），優於原估14+ | grep`_URL="http`逐檔計數 |
| 8輪資料品質不變量 | ✅LIVE | `v0.5.12` |
| CTA/多幣卡死互動修 | ✅LIVE | 已併main |
| 幣別/來源原廠LOGO、去slug品牌化 | ✅LIVE | `v0.5.9/10` |
| deploy gate修(probe/seed分離) | ✅LIVE | `956bae2` |
| 多幣總覽/成本透明/`/status` | ✅LIVE | `fetch_scheduler.py` |
| **商業級UI 4項**(表單斷/裸錯誤頁/無回首頁/卡片hover) | 🔨仍卡分支未merge main | `fix/ui-commercial`，2輪稽核皆列最優先未消化 |
| W3 informational訊號 | ✅LIVE | 簇flag全成員傳播 |
| W4三態骨架 | ✅LIVE(非嚴謹conformal) | docstring自陳 |

---

## B. 三軸 + 新Axis D多核心

**A呈現**：P1-P3 LIVE；P4/P5待做；商業級UI4項唯一「寫完沒上線」缺口。
**B深度**：W1.5 LIVE；**W2本輪啟用(最大進展)**；W3協同informational LIVE
真圖資料卡；W4三態LIVE真conformal資料卡(研究完成誠實不上線)。
**C廣度**：21源LIVE(優於前次10源)；第三批(CryptoPanic/Etherscan/Reddit
key)卡老闆申請，非技術缺口。

**Axis D多核心護城(新，本輪最大方向，`PLAN-multicore-worldfirst.md`)**
CP值排序：

| 排名 | 候選 | 可行性 | 需持久化 |
|---|---|---|---|
| 1 | #2多維度信任雷達 | 現在就能做，$0 | 否 |
| 2 | #3跨幣信任×操縱排行 | 接近現在能做(snapshot加manip欄位) | 否 |
| 3 | #1歷史信任PIT趨勢 | 需先做按日累積寫入 | 是 |
| 4 | #4來源動態信譽榜 | 依賴最深(先接出reputation_trace再疊) | 是 |

---

## C. 誠實資料卡/gated清單

| 項目 | 卡在哪 |
|---|---|
| 真Split Conformal | pseudo-AUC≈0.49等同隨機、缺異質歷史資料 |
| W3真帳號二部圖+Louvain | 無author欄位、單次3-7筆證據，做圖是假深度 |
| W1開源本地NLI | AWS-only合規待7/13 Mars Li確認，非拒絕是排隊 |
| W2真深度(token-gated materialize) | 需額外Bedrock預算，待評估成本 |
| C第三批資料密度 | 需老闆申請key，非技術缺口 |
| Axis D #1/#4持久化 | DynamoDB單item覆寫無日期維度，需架構決策 |
| 信任分資訊正確性判別力驗證 | 需人工標註歷史真偽事件集，尚未開始 |

---

## D. 下一步：連環疊的核心序

**第一梯隊(零等待立刻做)**
1. 商業級UI 4項merge+部署——卡2輪稽核，ROI最高風險最低，最優先。
2. Axis D #2多維度信任雷達——CP值最高，$0現有資料夠，立即建立差異化骨架。
3. Axis D #3跨幣信任×操縱排行——緊接#2疊上，複用分項邏輯，不需持久化。

**第二梯隊(本週內同步啟動)**
4. 啟動Axis D #1持久化寫入(先寫入UI晚做)——越早開始累積，PIT賣點越早成立。
5. Axis A P4差異化demo case：前提已滿足，找真實觸發案例。
6. 啟動信任分正確性判別力驗證方法論：人力密集，越早開始越好。

**第三梯隊(依賴解除後)**
7. Axis D #4來源信譽榜：待#1持久化+接出reputation_trace，最晚上線。
8. Axis C第三批：待老闆核准申請key。
9. 7/13合規結果後：W1開源NLI評估、真conformal/真協同圖持續資料卡。

---

## 完成回報

檔案：`/Users/apple/HurricaneSoft/trustforge/docs/plans/WORLD-FIRST-MASTER-PLAN.md`
（覆寫，唯一權威）；同步更新`docs/README.md`索引（補入
`PLAN-w2-enable-final.md`/`PLAN-data-density.md`/`PLAN-source-branding.md`/
`PLAN-multicore-worldfirst.md`）。
