# ROADMAP — 對齊黑客松里程碑

> 完成標準 = 8/1–2 現場能跑出有溯源、可查證的 Live Demo，且只用 AWS Bedrock。

## M0 — Repo 奠基（本輪，✅）
- [x] 三層架構（ingestion / trust / agent）
- [x] 信任提煉引擎可運作啟發式 + 測試
- [x] Bedrock 單一模型入口（offline 可跑）
- [x] **對齊官方規格**：5 幣種池、OHLCV CSV 連接器、3 題型
- [x] **4 交付件**：Report(事實→推論→結論) / Evidence(官方欄位) / Execution Log
- [x] **15 分鐘執行預算**追蹤 + 反作弊設計（判斷由我方 pipeline 產生）
- [x] AWS 架構文件（決賽簡報用）+ Kiro 加分標註
- [x] 13 測試全綠 + CLI 端到端產出交付件
- [x] GitHub（主）+ Gitea（鏡像）

## M1 — 工作坊前（~7/10，✅ 大致完成）
- [x] 申請競賽 AWS 帳號，確認 Bedrock 可用模型與區域（帳號 795930814369；Claude Sonnet/Haiku 4.x 雪梨 ap-southeast-2 + 東京可用；`au.anthropic.claude-*` inference profile）
- [x] 把附件 1/2（命題、資料文件）歸檔 docs/（`COMPETITION-OFFICIAL.md` / `COMPLIANCE-CHECK.md`）
- [ ] 報名工作坊、確認隊員與出席分工（真人事項，隊名「中再參與」已定，7/13 工作坊全員出席）

## M2 — 企業數據工作坊後（7/13~，🟡 進行中）
- [x] 接真實外部來源驗證連接器介面：price(OHLCV) / news RSS / onchain(blockchain.info) / SEC EDGAR FTS / Fear&Greed（6 類中 5 類真實通路，#155 已驗證）
- [x] 用 Bedrock 強化 claim 抽取與操縱偵測 / 語意 stance 佐證閘（W1.5 Bedrock Haiku entailment/contradiction，取代純詞彙重疊）
- [ ] 依 HOYA BIT 數據規格把 `ingestion/hoyabit.py` 從 stub 接真實資料源（stub 契約已完成 #154；真實接線待 7/13 工作坊 spec，追蹤 **#167**）
- [ ] Reddit 社群真實 OAuth（雲端 IP 429/403，待真人辦憑證，追蹤 **#8 / #153**）

## M3 — 進階工作坊後（7/19~，✅ 核心已完成）
- [x] 信任演算法深化：域內停用詞過濾、方向一致性閘、跨源訊號背離偵測、聰明錢背離、來源自我矛盾時間窗閘、canonical source dedup（多輪 #15/#4/#24/#56/#72/#149/#150 等）
- [x] Live Demo Web UI（商業級 dark dashboard：信任橫條 / 可展開證據+真 URL / 操縱旗標 / 信心儀表 / 信任四分項拆解 / 跨源訊號面板 / 洞察可解釋面板 / admin console）
- [x] 端到端跑通：query → 信任加權分析 → 溯源呈現（EC2 固定 EIP 生產上線，`?real=1` 真資料 credit-safe，15 分鐘預算 13× 餘裕）
- [ ] 決賽前商業級 UI 4 項驗證修復（dead cards / 破損比較表單 / 裸錯誤頁 / 無首頁連結，追蹤 **#172**）
- [ ] 決賽敘事強化 UI（分層評分 / Evidence Trail Cards / 信賴區間，追蹤 **#171**）

## M4 — 決賽（8/1–2，30 小時，⏳ 未開始）
- [ ] Trust Score 定位拍板（「完整度＋可溯源」vs「預測力」，待 Eric，**#168**）
- [ ] AWS Kiro +10% bonus 是否 claim（待 Eric，**#169**；先前一度定 won't-do，7/13 plan 重提評估）
- [ ] 以 8/1 現場公告調整交付範圍與資料
- [ ] 跑通可佈署實證 Demo，準備評審簡報（技術深度 + 創意可用性）
- [ ] 繳交：命題連結 / 企業數據應用 / 技術架構 / 生成式 AI 應用 / Live Demo

## 風險與守則
- **模型合規**：全程 Bedrock 直連，禁用其他供應商與內部閘道（anemone）。
- **企業數據保密**：HOYA BIT 資料不進公開版控（.gitignore 已擋），repo 設 private。
- **不給投資建議**：輸出定位為「可查證分析 + 反方證據」，非代客決策。
