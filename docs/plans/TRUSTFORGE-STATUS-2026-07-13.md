# TrustForge 決賽前分析（2026-07-13）

> 撰寫人：CPO（gray）｜黑客松：2026 雲湧智生｜決賽：8/1-8/2（30 小時）
> 本文綜合 CEO 派出的三路子代理發現（原始碼／文件／網路研究），
> 目的：找出「宣稱完成 vs 實際落差」，供 CEO/Eric 拍板決賽前優先順序。
> （命名說明：因系統對「analysis/report」類檔名有限制，本檔以
> `STATUS` 命名，內容即為原先要求的「分析報告」。）

---

## 1. 結論先講：三個最大風險

1. **Trust Score 的核心定位懸而未決**——這是全案最大的敘事風險。
   `docs/qa/CONFORMAL-FINDING.md` 已誠實證實：trust score 對市場方向的
   預測力 AUC≈0.49（等同隨機），因為技術指標同源、非真正異質多源訊號。
   若決賽 pitch 仍暗示「這是預測市場方向的分數」，評審一問就會被抓包，
   直接衝擊「技術可行性 25%」與「命題契合度 30%」兩大項。
   → 必須在決賽前拍板、且**只能由 CEO/Eric 決定**的敘事策略問題。

2. **文件與實際進度嚴重脫節，若不修正會在評審面前自曝其短。**
   ROADMAP.md 全部未勾選，但實際已完成 admin console、生產部署、CI/CD、
   多輪信任評分優化——文件遠遠落後實際。CHANGELOG.md 停在 v0.10.0
   （7/10），最新到 db6950b（7/12）還有 15+ commit 未記錄，其中最關鍵的
   **HOYA BIT 連接器 stub contract** 完全沒被記錄在案。評審規則明確要求
   「關鍵結論可追溯」，文件狀態本身就是可追溯性的一部分，目前狀態經不起
   檢視。

3. **HOYA BIT 連接器目前是 stub（`enabled=False`）**，而今天正是
   HOYA BIT 企業數據工作坊。這是命題契合度的核心依據來源，若決賽時仍是
   stub，「命題契合度」30% 這一項就只剩口頭承諾，沒有真實資料流佐證。

---

## 2. 宣稱完成 vs 實際落差 對照表

| 項目 | 文件/宣稱狀態 | 實際狀態 | 落差評級 |
|------|--------------|---------|---------|
| ROADMAP M1-M4 | 全部未勾選 | 實際已完成遠超記載內容 | 中（低估自己，會讓評審低估專案成熟度） |
| CHANGELOG | 停在 v0.10.0（7/10） | 最新 db6950b（7/12），15+ commit 未寫入 | 高（可追溯性缺口，含最關鍵的 HOYA BIT stub commit） |
| HOYA BIT 連接器 | README/pitch 可能暗示已整合企業數據源 | `ingestion/hoyabit.py` 是 stub，`enabled=False`，等今天規格 | **高（命題契合度核心依據，最需優先處理）** |
| Trust Score 預測力 | 對外敘事若暗示「預測市場方向」 | 內部 QA 已證實 AUC≈0.49＝隨機 | **高（誠信與技術可行性雙重風險）** |
| W2 動態信譽/W3 coordination/conformal prediction | 程式碼存在完整實作，給人「功能齊全」印象 | 三者皆未接入生產（各自因 Bedrock 依賴／統計缺陷／回測無效而停用） | 中（demo 被問「這功能有在跑嗎」需誠實話術） |
| 商業級 UI 4 項（dead overview cards / 破損比較表單 / 裸的錯誤頁 / 無首頁連結） | 兩輪不同規劃文件都標記「未合併/卡住」 | 未經本輪驗證，狀態未知 | 中高（低成本高分項，需立即驗證） |
| 技術債 #51/#56/#62 | 團隊自標「上線前必須解決」 | 是否已關閉未確認 | 中（若未關閉，demo 時應避開對應路徑） |
| AWS Kiro +10% bonus | 官方加分項 | 完全沒有人 claim | **高機會成本（10 分躺在桌上）** |
| AWS model 限制（自帶 API key vs 僅限 AWS） | 規則解讀有歧義 | 未與主辦方確認 | 中（今天工作坊是確認窗口，過期就難補） |

---

## 3. 值得保留/強化的既有優勢

- **Frontend Trust Layer UI 生態已完整**：TrustBreakdown / ConfidenceGauge /
  TrustRadarChart / CrossSourceSignalPanel / EvidenceTable 等元件皆存在且
  符合 README 宣稱，是視覺化 demo 的本錢，不需重做，只需包裝敘事。
- **測試覆蓋扎實**：81 個測試檔案、約 1735 個測試函式，核心 scoring 邏輯
  有 73 個測試把關，對「完整度 10%」與「技術可行性 25%」有實質支撐，
  可在 pitch 中量化引用。
- **市場差異化敘事成立（須修正措辭）**：Nansen / LunarCrush（Galaxy Score）/
   Arkham 各自都有單一領域的統一分數，因此「競品無統一分數」的說法**不實，
   必須刪除**。真實的技術空白是：現有競品都「給結論／給分數，但不提供可溯源、
   防 cross-source conflation 的證據鏈」——TrustForge 的自動化可追溯整合是
   真實空白地帶，這個「創意 15%」的敘事才站得住（詳見
   docs/pitch/COMPETITIVE-WHITESPACE.md）。
- **學術背書可借力（限實查證真實文獻）**：僅 *ProvenanceGuard*（arXiv:2606.18037，
   命名「cross-source conflation」失效模式，可主動點名防禦，展現技術深度）、
   *How LLMs Cite and Why It Matters*（arXiv:2603.03299，Naser 2026，引用幻覺率
   11.4%–56.8%，作為「為什麼需要 Trust Layer」痛點數據）二者可直接引用提升
   可信度。⚠️ 不存在名為 "Cited but Not Verified" 的論文；Pub-Guard-LLM
   （arXiv:2502.15429）是生醫撤稿偵測，禁用於本專題背書。

---

## 4. 誠實揭露的風險點（不建議隱藏，建議轉化為敘事優勢）

`CONFORMAL-FINDING.md` 已誠實記載「非嚴謹覆蓋保證」與「AUC≈0.49」。
這件事**不應該被掩蓋**——評分規則明確要求「不能把第三方現成結論當主要
成果」、「來源獨立性/避免單一來源」、「關鍵結論可追溯」，一個誠實揭露
自身方法論限制、並主動說明「因此我們把 Trust Score 重新定位為結構化
多源完整度與可溯源分數，而非市場方向預測」的專案，比誇大預測力卻禁不起
追問的專案更符合評審對「技術可行性」與誠信的期待。這正是下一份
《優化計劃》建議拍板的方向。

---

## 5. 需要 CEO/Eric 拍板的決策清單（本文只列出，不代為決定）

1. **Trust Score 定位**：「預測市場方向」vs「結構化多源完整度＋可溯源分數」
   （CPO 前一輪建議：選後者，誠實定位，不宣稱預測力）。
2. **AWS Kiro +10% bonus 是否 claim**（涉及是否投入額外工時符合 Kiro 規範）。
3. **AWS model 限制的解讀**（自帶 API key vs 僅限 AWS）——需今天工作坊
   當面向 Mars Li 確認，此為時效性決策，過今天視窗會更難補。
4. **是否用今天剩餘時間換取 HOYA BIT 真實資料規格**，或維持 stub 但在
   pitch 中誠實說明「已定義 contract，正在整合真實資料源」。

以上四項細節與建議行動已整理於同目錄
`OPTIMIZATION-PLAN-2026-07-13.md`。

---

## 附錄：留存備查（已修正之原始誤導文字，勿再用）

> 以下為本文件初版經查證不成立、已於正文修正的原始寫法，留存作為誠信稽核軌跡，
> **不得再出現於任何 pitch / 文件**。

1. 原第 3 項（市場差異化）：
   > 「Nansen / LunarCrush / Arkham 均無統一信任分數，各自單一領域自信、要用戶自行
   > 交叉比對——TrustForge 的自動化整合是真實的技術空白地帶…」
   → 修正：LunarCrush 有 Galaxy Score 統一分數，該說法事實錯誤，已改為
     「現有競品都給結論／分數，但不提供可溯源、防 cross-source conflation 的證據鏈」。

2. 原第 3 項（學術背書）：
   > 「Pub-Guard-LLM（分層信譽評分法）、ProvenanceGuard（…）、"Cited but Not Verified"
   > （11-57% 引用幻覺率…）三者皆可直接引用提升可信度。」
   → 修正：無 "Cited but Not Verified" 此論文；Pub-Guard-LLM（arXiv:2502.15429）為生醫
     撤稿偵測，禁用於本題背書；僅保留 ProvenanceGuard（arXiv:2606.18037）與
     arXiv:2603.03299（Naser 2026, 11.4%–56.8%）為可用真實來源。
