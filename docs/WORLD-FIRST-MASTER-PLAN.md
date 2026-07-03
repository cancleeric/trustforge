# TrustForge — 世界第一 Master 開發計劃（三軸總綱，v2 全面稽核版）

> 作者：CPO（gray）｜建立：2026-07-02｜**本次全面更新：2026-07-03**
> 依據：`docs/DEV-PLAN-REWRITE.md`（Axis A 全文）、`docs/WORLD-FIRST-ANALYSIS.md`
> （Axis B 研究/決策日誌）、`docs/PLAN-w2-wiring.md`（W2 接線細節）、
> `docs/CONFORMAL-FINDING.md`（W4 conformal 誠實負結果）、
> `docs/PLAN-W3-coordination-graph.md`（W3 協同圖資料卡）、
> `docs/OPTIMIZATION-PLAN-weakness.md`（CEO 兩路批判彙整）、`docs/QA-PLAN.md`
> （P-2026 CTA 事故根因＋測試補強）、`docs/COMPLIANCE-CHECK.md`／
> `docs/COMPETITION-OFFICIAL.md`（合規紅線）、`ROADMAP.md`（黑客松里程碑）、
> **本輪逐檔 grep 實證 + `pytest -q` 實跑（950 passed / 6 skipped，0 failed，
> 2026-07-03）+ `curl http://13.211.110.218/` 生產驗證**（見各節「證據」）。
> 定位：**本文件是總綱、不重寫細節**——各軸逐 Phase 施作細節仍以對應子文件
> 為準，本文件負責「三軸放一起看、排優先序、標誠實進度、抓合規風險」。

---

## 最終標準宣言（世界第一非商業 floor）

**老闆定調，本文件全程遵守**：
> 世界第一才是目標，商業等級只是起點；黑客松是全台最強駭客併命賽，
> 不世界第一贏不了；題目只是方向，別被題目框死。

因此本文件的每一項判定，都用「這是不是世界第一級」而非「這樣夠不夠交差」
來衡量。凡是「能做到但還沒做」「已做但是簡化版/heuristic」「因資料/合規
限制暫時做不到」三種狀態，**一律誠實標出，不混為一談，也不因為已經『比
上次好』就自滿**。commercial-grade UI 不互動、裸錯誤頁——這是**世界第一
的最低門檻**，不是加分項；連這個都沒做好，後面的演算法深度無從被評審
看見。

---

## 0. 對話關鍵決策整理（給後續讀者的快速上下文，時間序）

1. **老闆親測 LIVE 版否定「一點都不專」** → 觸發 Axis A 呈現層重寫計劃
   （`DEV-PLAN-REWRITE.md`），5 階段拆分（P1-P5）。
2. **老闆「重新分問」否定「核心引擎已到頂」的自滿結論** → 觸發 Axis B 四路
   研究（學術 SOTA / crypto 大廠 / 信任 UX 大廠 / issue triage），拍板軸線
   ＝**演算法深度 × 可解釋性**（`WORLD-FIRST-ANALYSIS.md`）。
3. **官方錄取信硬約束確認**：「僅限使用 AWS 服務提供之基礎模型」——曾一度
   誤判為「非官方自我約束」，後由老闆轉來官方原文更正。`COMPLIANCE-CHECK.md`
   flag #1（2026-07-01 記錄）**至今（07-03）仍未拍板**：「命題文件 vs 錄取信
   兩種解讀不一致，7/13 向窗口 Mars Li 確認定案」——**尚未解除，仍是待確認
   風險**，10 天後才有答案。
4. **W1.5 已上線**：跨源佐證語意層用 **Bedrock Haiku 逐對 stance 分類器**
   （AWS FM 合規），非本地開源模型。`orchestrator.py` 已接
   `shared_stance_fn`（L774），`trust/scoring.py::_corroboration`（L693-729）
   為 token 重疊+停用詞+方向閘前置閘 **+** stance_fn 二次確認矛盾，是混合式。
5. **W2（動態來源信譽）引擎完整、預設仍關**——`grep dynamic_reputation`
   實測：`scoring.score()` 簽名預設 `dynamic_reputation: bool = False`
   （L1116），`orchestrator.py` L778 呼叫 `score()` **未帶**該參數，走
   default False。`docs/PLAN-w2-wiring.md` 方案仍完整、仍是現成可執行計劃，
   **10 天過去了仍未執行**——這是本輪稽核發現的**最大單一浪費**：零成本、
   零新設計、已審過的方案放著沒接線。
6. **Phase2（Axis A 預設真資料）已完整上線**——`release/v0.5.6`（`da943ad`）
   已含 Phase2「預設切真資料+HOYA 日期修+缺源優雅+限流隔離」全套（PR #44），
   **不再是「執行中」，是完成狀態**，此為本次稽核最大的一項「上次標記過舊」
   修正。
7. **Phase3（資訊架構/視覺可信度）已完整上線**——`release/v0.5.7`
   （`848b1f4`）含 `ff1652e`（PR #46：loading/字體階層/mobile/來源標籤），
   **同樣是「上次標記過舊」的修正**，舊版文件仍標 P3 ❌ 未做，實際已 LIVE。
8. **Axis C #1（歷史信任分快照）已上線**——`a453a09`（PR #47，已收錄進
   v0.5.7）：`scripts/fetch_scheduler.py --snapshot` 寫多幣快照 + 首頁總覽
   （背景刷新/in-memory 自失效），**不再是純設計/roadmap，是可運作的
   production 功能**，同樣是「上次標記過舊」的修正。
9. **W3 informational 訊號誠實升級**——`fe023e4`（PR #49，已收錄進
   v0.5.7）：#16 相似簇 flag 傳播至全體成員（非只 hub）已完成；但**真正的
   「帳號-內容二部圖 + Louvain 社群偵測」經 grep 實證判定為資料卡**
   （`docs/PLAN-W3-coordination-graph.md`）——連接器完全沒有 author/account
   欄位，一次查詢僅 3-7 筆證據、10 個 source 類別，做圖會是「假深度」，
   **舊版「✅能做」的判定本身是錯的，本輪已修正為誠實資料卡**。
10. **W4 conformal 已完成研究、誠實不上線**——`docs/CONFORMAL-FINDING.md`：
    真的做出了 split conformal 數學實作（`trust/conformal.py`、
    `scripts/backtest_conformal.py`，8 個測試），JOINT coverage 數學上達標
    （0.0400 ≤ α=0.10），但價格代理訊號對「3 日後方向判斷」pseudo-AUC≈
    **0.492（等同隨機）**，held-out abstain 率 0.9405——接進 production 等於
    廢掉功能。**production 維持簡化分位數校準，不接 conformal τ**，這是本
    專案至今**最誠實的一次負結果**，完全符合 #24 不造假。
11. **P-2026 生產 UX 事故：CTA 死互動**——老闆親自用 Chrome 點出「桌面版
    hero CTA『立即開始分析』點擊零視覺反應」，`pytest -q`（937 passed 全綠）
    完全沒抓到，因為**測試把 bug 設計本身斷言成預期行為**
    （`test_render_home_page_has_query_console_cta` 只驗證 href 字串存在）。
    觸發 `docs/QA-PLAN.md` 全面補強連結/CTA/表單旅程測試缺口。
12. **CEO 兩路批判彙整（`OPTIMIZATION-PLAN-weakness.md`）**：核心弱點
    分析（信任分效度、資料密度、niche）+ UI code-grounded 審查（表單斷/
    裸錯誤頁/無回首頁/卡片無 hover），**Phase1 UI 快修已寫成計劃，本輪稽核
    確認：hero CTA 已修 LIVE，其餘 4 項（比較表單斷/裸錯誤頁/無回首頁/卡片
    hover）已在 `fix/ui-commercial` 分支寫完（`66f2d21`），但**尚未 merge、
    尚未上生產**——這是本輪稽核發現的**第二大缺口**：修好的東西卡在分支
    沒上線。
13. **核心定位釘死（本輪明確重申）**：信任分＝資訊可信度（信譽×佐證×時效
    −操縱），**不是價格預測**。`PROPOSAL.md` L11 已明文「這道題的核心
    **不是**準確預測幣價」——**這個定位從一開始就是對的，W4 conformal
    「對價格 AUC≈0.49」驗證的是「用價格代理訊號預測方向」這件事本身沒有
    判別力，不是「信任分沒用」**。世界第一級的正確驗證方式應該是「高信任分
    資訊是否被多源佐證/後續證實為真」（對**資訊正確性**的判別力，不是對
    **價格漲跌**的判別力）——這件事本專案**尚未做**，見 §B.1。

---

## A. 逐項完成度總表（grep 實證，2026-07-03）

> 標記：✅完成LIVE（已在生產 v0.5.7 或以前版本上線）／🔨進行（有分支但未
> merge 或未部署）／❌未做／📋roadmap資料卡（有明確理由暫不可行，非偷懶）

### A.1 Tier1 四大護城河（Axis B 核心引擎）

| 項目 | 現況 | 證據 | 狀態 |
|---|---|---|---|
| **W1.5 語意 stance** | Bedrock Haiku 逐對 stance 分類，`_StanceBudget` 節流+cache，跟 Step2 矛盾閘/Step3 跨源 stance_pairs 共用同一預算實例 | `orchestrator.py:774-785`；`shared_stance_fn = build_stance_fn(...)` | ✅完成LIVE |
| **W2 truth-discovery（動態來源信譽）** | 引擎完整（PR #29，284 測試綠+4 輪 codex 對抗審），`docs/PLAN-w2-wiring.md` 接線方案完整，但**預設仍關** | `scoring.py:1116 dynamic_reputation: bool = False`；`grep dynamic_reputation orchestrator.py` **零命中**（未帶參數呼叫） | 🔨進行（**引擎完成、接線 10 天未做，見 §B.2 缺弱**） |
| **W3 協同偵測** | informational 訊號：模板相似 Jaccard（跨源）+ #16 簇 flag 傳播全成員（PR #49，已上線）；burst 爆量偵測寫完但停用（`_coordination_signals` 明確註解降級）；**真「帳號-內容二部圖+Louvain」= 資料卡**（連接器無 author 欄位、每次 3-7 筆證據/10 source 類別，做圖是假深度） | `scoring.py:419`（template）、`:585`（burst，停用）、`:664`（signals，只整合 A）；`docs/PLAN-W3-coordination-graph.md` 逐檔 grep 表 | ✅完成LIVE（informational 訊號）／📋roadmap資料卡（真協同圖） |
| **W4 校準+abstain 三態** | production：簡化分位數校準 + 硬門檻 abstain，docstring 自陳「非嚴謹 conformal」；**真 Split Conformal 已研究完成**（`trust/conformal.py`+8 測試，τ=0.9154，JOINT coverage 達標）但 pseudo-AUC≈0.49（等同隨機）、abstain 率 94%，**誠實決定不接進 production** | `docs/CONFORMAL-FINDING.md` 全文；`scripts/backtest_conformal.py` 可重現 | ✅完成LIVE（三態骨架）／📋roadmap資料卡（真 conformal，理由：代理訊號同源、缺異質歷史資料） |

**Tier1 小結**：4 項中，W1.5 完整上線、W3/W4 皆完成「該做的免費確定性層」
且對「做不到的更深層」給出誠實資料卡（不是敷衍，是真的 grep+回測驗證後
判定資料/方法論不支撐）；**唯一真正「能做但沒做」的是 W2 接線**——方案
已審完 10 天，零成本、零新設計，是本輪稽核最該立刻處理的一項。

### A.2 Tier2 UX（可解釋性）

| 項目 | 現況 | 證據 | 狀態 |
|---|---|---|---|
| 逐項 WHY（事實→推論→結論） | `Report.market_judgment`/`key_basis`（`BasisItem`）分區呈現 | `COMPLIANCE-CHECK.md` A 表；`ARCHITECTURE.md` | ✅完成LIVE |
| 結構化分歧（跨源背離/共識） | `cross_source_signal` | `PROPOSAL.md` 對標表 L33 | ✅完成LIVE |
| 來源 pill / tier 標籤 | Phase3 視覺可信度打磨已含「來源標籤」 | `ff1652e`（PR #46）已收錄 v0.5.7 | ✅完成LIVE |
| 操縱 🚩 | `manip_flags`（確定判定）＋ `info_flags`（中性提醒，非指控，不扣分） | `scoring.py:133-138` | ✅完成LIVE |

### A.3 CoinGecko 資料管線

| 項目 | 現況 | 證據 | 狀態 |
|---|---|---|---|
| 現價/情緒/開發 3 類端點 | `coingecko-price`/`-sentiment`/`-dev` 三 source | `ingestion/coingecko.py` | ✅完成LIVE |
| 429 節流 | `_throttle_before_request` 共用配額池，一失敗全池記住 | `coingecko.py:32,201,280` | ✅完成LIVE |
| 308 / SSRF 加固 | 白名單來源＋`safe_fetch.py`（逐跳驗證+DNS pinning+禁自動跟轉），對抗審 3-4 輪修 | `coingecko.py:3,76,271-284`；commits `55c7f61`/`18e1f6d`/`ac1066d` | ✅完成LIVE（`release/v0.5.5`） |

### A.4 Axis A 呈現層（`DEV-PLAN-REWRITE.md` P1-P5）

| Phase | 內容 | 現況（本輪修正） | 證據 |
|---|---|---|---|
| **P1** | 拔 dev artifacts、首頁不空白 | ✅完成LIVE（v0.5.4） | `7f3064a` |
| **P2** | 預設切真資料+健康 gate+日期修正+缺源優雅 | ✅完成LIVE（v0.5.6）**——舊版誤標「執行中」，本輪修正** | `30efae1`/`da943ad` |
| **P3** | 資訊架構/視覺可信度（loading/字體階層/mobile/來源標籤） | ✅完成LIVE（v0.5.7）**——舊版誤標「未做」，本輪修正** | `ff1652e` |
| **P4** | 差異化 demo case（已知觸發案例+誠實未觸發文案） | ❌未做，`git log --all` 逐字/語意搜尋無對應 commit | 依賴 P2 真資料已上線，前提已滿足，**可以排程了** |
| **P5** | 結果持久化+主題重開 | ❌未做，roadmap | 依賴持久化架構決策，未動 |

**首頁 CTA 死互動（P-2026 事故）**：hero「立即開始分析」已從死錨點
`#tf-query-console` 改為真的觸發 `/analyze` 分析（`_hero_analyze_href()`，
`web.py:1224-1232`），**✅完成LIVE 在 `develop` HEAD（`f87fce4`），但尚未
merge 進 release 分支、尚未部署上生產**（生產目前仍是 v0.5.7，未含此修
—— `curl http://13.211.110.218/status` 實測回傳 `v0.5.7`）——**這是「已修好
但沒上線」的狀態，需注意跟「已上線」區分**。

### A.5 Axis C 廣度

| 項目 | 現況 | 證據 | 狀態 |
|---|---|---|---|
| 多幣快照+總覽（4.1） | `fetch_scheduler.py --snapshot`（背景刷新/in-memory 自失效），已收錄 v0.5.7 | `a453a09`（PR #47） | ✅完成LIVE**——舊版誤標「未做」，本輪修正** |
| 變動/分歧告警+watchlist（4.2） | 依賴 4.1 累積數天資料 | 尚無足夠天數的累積資料可驗證 | ❌未做（依賴項已就緒，可排程） |
| 訊號操縱透明擴展（4.3） | 技術面依賴 W3 輸出，敘事面待一手來源確認 | `PLAN-W3-coordination-graph.md` | ❌未做 |
| 免費開放 API（4.4） | `/analyze.json` 站內用途，非公開文件化 | — | 📋roadmap（需老闆先拍板是否對外開放） |

### A.6 誠實硬化

| 項目 | 現況 | 證據 | 狀態 |
|---|---|---|---|
| 時間戳 robustness（#12） | 未來戳/NaN/±inf 三層防禦，不虛增信任（`math.isfinite` 全域防禦） | `scoring.py:241-260`；`a759184`（PR #48）+`654a5ab`/`bf7e898`/`af018aa` 三輪對抗審 | ✅完成LIVE（v0.5.7） |
| 限流 | `TRUSTFORGE_LIVE_TOKEN` gate、`COST_BUDGET_USD` 預算、real-off 檔位獨立寬鬆限流 | `web.py:46,48`；`1d2460d` | ✅完成LIVE |
| 日期 provenance | 查詢文字改回 date-agnostic，日期只在結果頁 provenance 顯示 | `4ba29da`/`326c281` | ✅完成LIVE |

### A.7 商業級最低標 UI（CEO Chrome 親測點出，`OPTIMIZATION-PLAN-weakness.md`）

| # | 問題 | 現況（本輪 grep 實證） | 狀態 |
|---|---|---|---|
| 1 | hero CTA 死互動 | `_hero_analyze_href()` 已改真連結 | 🔨進行（develop 已修，**未部署上生產**） |
| 2 | 多幣卡死 `<div>` 無 hover | `fetch_scheduler.py` 已包 `<a class="tf-overview-card">`（`a453a09`）；但 `web.py` **無 `.tf-overview-card:hover` CSS**（grep 零命中）——卡片可點但視覺上看不出可點 | ✅部分完成LIVE（可點）／🔨進行（hover 視覺區隔在 `fix/ui-commercial` 分支未 merge） |
| 3 | 比較分析表單斷（選比較分析送出即 `ValueError` 洩漏 `coin=BTC,ETH`） | 修法已寫在 `fix/ui-commercial`（`66f2d21`：新增常駐 `coin2` 下拉），**尚未 merge、未上生產** | 🔨進行（未上線） |
| 4 | 裸錯誤頁（429/400/502/404 純 `<p style=color:#c00>`） | `_render_error_card()` 已寫在 `fix/ui-commercial`，**尚未 merge、未上生產** | 🔨進行（未上線） |
| 5 | 內頁無回首頁（logo 非連結） | logo 改 `<a href="/">` 已寫在 `fix/ui-commercial`，**尚未 merge、未上生產** | 🔨進行（未上線） |

**驗證方式**：`git branch --all --contains 66f2d21` → 僅 `fix/ui-commercial`
（本地+origin），不在 `develop`/`main`/任何 `release/*`；`curl -s -o /dev/null
-w "%{http_code}" http://13.211.110.218/` → 200，`/status` 版本 `v0.5.7`，
確認生產環境不含這批修復。

**A 節總結（本輪最重要的兩個修正）**：
1. 上一版文件把 P2/P3/Axis C 4.1/#16 標記過舊（誤標未完成），**本輪已用
   commit/release tag 逐一核實更正為 ✅完成LIVE**——文件本身也要接受「誠實
   不誇大也不低估」的稽核紀律。
2. **商業級最低標的 4 項 UI 修復已經寫完（`fix/ui-commercial`），但卡在
   未 merge、未部署**——這是「知道問題、方案寫完、卻沒有把最後一哩路走完」
   的典型浪費，優先序應該最高（見 §D 下一步）。

---

## B. 修正缺弱（誠實、grounded）

### B.1 核心定位釘死：信任分效度驗證方法論尚未建立（最關鍵的缺弱）

**現況**：`PROPOSAL.md` 從第一版就寫明「這道題的核心**不是**準確預測幣
價」，定位始終正確。但 W4 conformal 研究（`CONFORMAL-FINDING.md`）測的是
「用價格代理訊號預測 3 日後方向」，pseudo-AUC≈0.49——**這驗證的是『用同源
價格衍生訊號做方向預測』這件事沒有判別力，跟『信任分本身有沒有用』是兩
個不同的命題，不能把前者的負結果當成後者失敗的證據**。

**世界第一級該做但目前完全沒做的驗證**：
> 高信任分（高 confidence / 通過 abstain 門檻）的**資訊主張**，是否確實
> 「被更多獨立來源佐證」或「後續被證實為真、未被闢謠」——這是對**資訊
> 正確性**的判別力，不是對**價格漲跌**的判別力。

**為什麼現在做不到（誠實，非藉口）**：
- 需要「已知結論為真/為假的歷史加密市場資訊事件」標註資料集（如：某次
  監管公告後續被證實/被撤回、某次社群謠言後續被闢謠），**目前沒有這樣的
  標註資料集，且需要人工查證構建，不能靠現有連接器自動生成**。
- 連接器目前只 cache 現值快照，無歷史時序（`CONFORMAL-FINDING.md` (c)
  已指出同樣的資料缺口）。

**方法論設計（本輪提出，供排程，不是本輪承諾完成）**：
1. 人工挑選 20-30 個「有明確後續真偽結果」的歷史加密市場資訊事件（如：
   SEC 對某項目的執法公告、交易所官方闢謠公告——這些是可查證、有一手
   來源的公開歷史事件，不是憑空捏造）。
2. 對每個事件，用當時可得的多源證據跑一次 `score()`，記錄該事件相關
   claim 的信任分/confidence。
3. 驗證假設：信任分高的 claim，是否確實對應「後續證實為真」的比例顯著
   高於信任分低的 claim（用類似 AUC 的判別力指標，但這次的 label 是
   「資訊真偽」而非「價格漲跌」）。
4. 這個資料集構建工作量不小（需人工查證每個事件的後續真偽結果，且要
   遵守 #24 不能為了湊出好看結果而挑選事件），**列入 roadmap，非本輪
   CTO 可派工作，需要先有人力/時程規劃**。

**優先序**：高（這是整個信任分敘事的科學嚴謹性根基），但**不阻擋**其他
軸線的並行推進——不是「沒做完這個就不能做別的」，而是「這是需要盡快
排上時程的獨立研究工作」。

### B.2 W2 已就緒方案 10 天未執行（浪費，應立刻處理）

**現況**：`docs/PLAN-w2-wiring.md`（2026-07-01）已完成前提驗證（BTC 真
樣本接線後 confidence 0.6125→0.6279 可見變化）、接線方案（PR-A/PR-B 拆分）、
驗收標準、風險防護，**是現成可執行方案**。本輪（07-03）grep 確認
`orchestrator.py` 仍未帶 `dynamic_reputation=True`，**距離方案審完已過
10 天，零進展**。

**修正**：這不是「需要更多規劃」的項目，是「需要立刻排程執行」的項目。
本文件重申：**PLAN-w2-wiring.md 方案不需要重新設計，直接照方案派 CTO
執行 PR-A（merge 引擎進 main，預設仍關，零行為影響）→ PR-B（接線
`dynamic_reputation=True`）**。

### B.3 商業級 UI 修復卡在未合併分支（緊急，阻擋評審第一印象）

見 §A.7。`fix/ui-commercial`（`66f2d21`）已完整寫好比較分析表單修復、
統一錯誤卡片、logo 回首頁、卡片 hover 區隔，**距離上生產只差 merge +
部署**。這 4 項是 `OPTIMIZATION-PLAN-weakness.md` 明確點名的「防買家/
評審打槍」商業級最低標，**現在還差臨門一腳沒上線**。

**修正**：本文件把此項優先序提到 Axis A 所有項目最前——**比 P4 demo
case 更急，因為 P4 是加分項、這 4 項是「不做就會被扣分」的下限**。

### B.4 W3 判定翻案：舊版「✅能做」是錯的，本輪已修正為資料卡

**現況修正**：master 計劃 v1 §3.3 原文寫「✅ 能做，`networkx` Louvain
皆為免費確定性圖算法」，這個判定**沒有先查連接器實際回傳什麼資料**。
`docs/PLAN-W3-coordination-graph.md` 做了逐檔 grep 後判定：**現有資料
完全沒有 author/account 欄位，一次查詢只有 3-7 筆證據、10 個 source
類別**——做帳號級二部圖是「為了做圖而做圖，產出假深度」，違反 #24。

**這件事本身是一個好案例**：上一輪文件的「✅能做」判定沒有先 grep 實證，
本輪透過強制 grep 驗證抓出了這個錯誤——**這正是本次稽核「先 grep 實證
再判」要求存在的原因**，往後任何「✅能做」的判定都必須先有 grep/回測
證據，不能只憑演算法本身理論上可行就下判定。

### B.5 深度天花板誠實：已標的資料卡/合規卡清單（不裝深度）

| 項目 | 卡在哪 | 對外用語 |
|---|---|---|
| 真 Split Conformal Prediction | 代理訊號同源、pseudo-AUC≈0.49、缺異質歷史資料 | roadmap，需先有歷史多源資料管線 |
| 帳號-內容二部圖+Louvain 協同偵測 | 連接器無 author 欄位，節點數量級不足 | roadmap，需帳號級 firehose（OAuth/付費 API），列 post-competition |
| 開源本地 NLI（換掉 Bedrock Haiku） | AWS-only 合規紅線待 7/13 確認 | 待確認，非拒絕、是排隊 |
| 信任分對「資訊正確性」判別力驗證 | 需人工標註歷史真偽事件資料集，尚未構建 | roadmap，見 §B.1 |
| 大廠護城河（錢包標記/法院級歸因/機構基建/多鏈全量） | 需要 Arkham/Glassnode/Nansen 等級的長期人工/爬蟲/資料工程投入 | 誠實標記短期做不到，非本專案定位 |

---

## C. 擴充可強化（往世界第一，別停在 floor）

### C.1 資料密度：從 ~7 證據/5 源拉到 20+

**現況（grep 實證）**：一次真實分析（`out/real_btc/evidence.json`）
**7 筆證據、5 個相異 source**（`ohlcv-csv`×3、`glassnode`、
`hoyabit-ticker`、`coindesk`、`x-anon-42`）。10 個 source 類別是全池子
上限（news 3 個媒體值+reddit 2 個子版值+regulatory 1 個+onchain 2 個+
coingecko 3 個）。「多源」目前名副其實但**薄**，世界第一級應該更厚。

**可加免費真源清單**（`OPTIMIZATION-PLAN-weakness.md` §b 已列，本輪補充
grep 可行性判定）：

| 來源 | 成本 | 現況 | 可行性 |
|---|---|---|---|
| CryptoPanic 全量（現僅取子集） | 免費 tier | `ingestion/news.py` 已有 CryptoPanic 連接器基礎，僅需擴大取用範圍 | 高，改參數即可 |
| Etherscan 免費 API | 免費（需 API key） | 尚無連接器，`onchain.py` 目前只接 Fear&Greed/Blockchain.info | 中，需新增連接器邏輯，5 req/s 免費層限額需查最新官方文件 |
| 更多產業 RSS（交易所公告、監管機構） | $0 | `news.py`/`regulatory.py` 已有 RSS/Atom 解析基礎可複用 | 高，成本最低，優先做 |
| Reddit OAuth（解決 cloud IP 403 + 拿到更多筆數） | 免費（OAuth 額度內） | `social.py` 註解已自陳「cloud IP 可能 403，生產可靠存取需 OAuth（待辦）」 | 中，需申請 OAuth app，非純參數修改 |

**優先序建議**：RSS 擴充（$0、最低門檻）→ CryptoPanic 全量（改參數）→
Etherscan（新連接器，中等工作量）→ Reddit OAuth（需要額外申請流程）。

### C.2 信任分效度驗證：見 §B.1，已提出完整方法論設計，待排程。

### C.3 Niche / 差異化：逐主張可解釋信任是大廠空白

**已有論述（`WORLD-FIRST-ANALYSIS.md`/`PROPOSAL.md`）**：逐主張可解釋
信任（大廠是黑箱分數）、跨源分歧量化（Ground News blindspot 精神）、
訊號層操縱透明（比 Arkham Oracle 的黑箱推理更可解釋）。

**本輪補充（`OPTIMIZATION-PLAN-weakness.md` §c）**：
1. 「證據可追溯的多源彙整工具」定位——不比信任分預測力，比「每個結論
   都能點回原始來源」的透明度。
2. 特定幣種/題型深耕（如只做「監管公告+官方公告」交叉驗證），範圍窄但
   每條做深，而非現在 6 產品線都淺。
3. 教育/新手向定位——幫不熟悉多來源查證的使用者組織分散資訊，不跟專業
   分析平台拼深度，拼易用性。

**需老闆拍板**：三個候選方向互斥資源投入（做深某一項會犧牲廣度展示），
建議在 7/13 合規結果出來前先定調方向，避免決賽前臨時轉向。

### C.4 其他可延伸的世界級互動強化

- Evidence `<details>` 已展開做對（`QA-PLAN.md` §2 已確認驗收基準），
  可延伸：evidence 卡片內直接顯示「與其他 N 個來源的一致/矛盾關係」
  （複用 W1.5 stance 分類結果，非新演算法，屬 UX 呈現層擴充）。
- W3 #15（burst 爆量偵測）重新設計：`_coordination_burst_flags` 已寫完
  但停用，`docs/PLAN-W3-coordination-graph.md` 已列出 codex 對抗審發現
  的具體缺陷（固定牆鐘分桶可繞、baseline 對齊），修正後可重新啟用——
  是現有資料唯一能往下深化的「來源級」爆量偵測（非帳號級）。
- 覆蓋率量測（`QA-PLAN.md` §5）：`pytest-cov` 未安裝，web.py 互動層測試
  覆蓋是新戰場，建議加裝並設分層門檻。

---

## D. 三軸整合優先序（本輪重排，取代舊版排序）

> 對齊決賽時程：7/13 企業數據工作坊（合規確認+HOYA BIT 真數據）、
> 8/1-8/2 決賽（30 小時）。

### 第一梯隊（立刻做，本週內，零等待條件）

1. **商業級 UI 4 項修復上線**（§B.3）：`fix/ui-commercial` merge + 部署，
   解除「評審一眼看到裸錯誤頁/斷表單」的扣分風險。**這是目前唯一「已經
   寫完只差按下部署」的項目，優先序理應最高**。
2. **W2 接線 PR-A/PR-B**（§B.2）：方案審完 10 天未動，零成本、零新設計，
   立刻排程消化掉。
3. **hero CTA 修復隨上述一起部署上生產**（目前只在 develop，未上線）。

### 第二梯隊（本週-7/13 前）

4. **Axis A P4 差異化 demo case**：前提（P2 真資料上線）已滿足，可以開始
   找真實觸發案例（附時間戳可回溯，不得為展示放寬閾值）。
5. **W3 #15 burst 重新設計**：現有資料撐得住、免費確定性，依 codex 已列
   缺陷逐項修正即可重新啟用。
6. **C.1 資料密度擴充**：優先做 RSS 擴充（$0）+ CryptoPanic 全量（改參數）。
7. **§B.1 信任分效度驗證方法論**：開始構建歷史真偽事件標註資料集（人力
   密集，越早開始越好，不會在短期內出結果，但現在不開始會持續是敘事
   上的空白）。

### 第三梯隊（7/13 之後，拿到合規確認）

8. **W1 開源 NLI 評估**：依 Mars Li 回覆決定是否可評估，或維持 W1.5 現狀。
9. **Axis C 4.2 告警**：4.1 快照已上線，累積足夠天數資料後接續。
10. **Niche 定位拍板**（§C.3）：需老闆決定三選一方向。

### 第四梯隊（決賽前最後衝刺/決賽後）

11. **Axis A P5**（持久化+主題）：留到最後，明確依賴前置架構決策。
12. **真協同圖/真 conformal/信任分效度大規模驗證**：全部列
    post-competition roadmap，需要的資料基礎設施非短期可建。
13. **Axis C 4.3/4.4**：依賴 W3 完成度/老闆拍板。

---

## 守則重申（三軸共同紅線，不變）

1. **#24 不造假**：demo case 必須真實觸發、有時間戳可回溯；測試合成
   資料是標準工程實踐，不等同造假，但不得把合成測試資料當展示資料。
2. **credit-safe**：新增付費資源需先估算成本並經 CEO/老闆核准。
3. **GitFlow**：分支+PR+CEO Chrome 親測驗收，不可只信自動化測試綠燈——
   P-2026 CTA 事故是活教訓：`pytest -q` 937 passed 全綠，仍漏掉真實 UX
   bug，因為測試斷言了 bug 本身的設計。
4. **誠實不誇大也不低估**：本輪稽核發現舊版文件把 P2/P3/Axis C 4.1/W3
   #16 標記過舊（該標 ✅ 卻仍標未完成/進行中），已逐一用 commit/release
   tag 核實更正——**誠實紀律也包括「不要因為謹慎而把已完成的東西低估
   成沒做」，這樣同樣會誤導後續排程判斷**。
5. **先 grep/回測實證再判定可行性**：W3 協同圖翻案（§B.4）是本輪最重要
   的方法論教訓——「理論上可行」不等於「這個專案的現有資料撐得住」，
   任何新的「✅能做」判定都要先有 grep/回測證據。

---

## 下一步該做的 5 件事（往世界第一）

1. **商業級 UI 4 項修復 merge+部署上生產**（`fix/ui-commercial` →
   release）——已寫完，只差最後一哩路，是目前 ROI 最高、風險最低、
   最急迫的一項（阻擋評審第一印象）。
2. **W2 動態來源信譽接線 PR-A/PR-B**（`docs/PLAN-w2-wiring.md`）——方案
   審完 10 天未執行，零成本、零新設計，立刻排程消化。
3. **啟動信任分「資訊正確性判別力」驗證方法論**（§B.1）——這是整個
   產品科學嚴謹性的根基，且是「W4 對價格 AUC≈0.49 不等於信任分沒用」
   這個關鍵論述的唯一救贖，需要人工構建歷史真偽事件資料集，越早開始
   越好。
4. **Axis A P4 差異化 demo case**：前提已滿足（P2 真資料已上線 10 天），
   找到至少 1 個真實觸發案例（附時間戳可回溯），把「這系統真的會抓到
   異常」變成可展示的證據，而非停留在演算法描述。
5. **資料密度擴充（RSS + CryptoPanic 全量）**：把「多源」從現在的 ~7
   證據/5 源真正拉厚到 20+，這是評審現場最容易感知到「深度」的具體
   數字，且成本 $0、技術門檻低，沒有理由拖到決賽前才做。

---

## 完成回報

**檔案路徑**：`/Users/apple/HurricaneSoft/trustforge/docs/WORLD-FIRST-MASTER-PLAN.md`
（覆寫更新，唯一權威）；同步更新
`/Users/apple/HurricaneSoft/trustforge/docs/README.md`（索引行）。
