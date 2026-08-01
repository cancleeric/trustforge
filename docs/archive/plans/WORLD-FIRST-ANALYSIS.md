# TrustForge — 世界第一 gap 分析與策略（研究落地）

> 建立：2026-07-01 ｜ 作者：CEO（HurricaneSoft）｜ 觸發：老闆 Eric「重新分問——開發到這就世界第一了？研究論文/大廠產品看過沒有？」
> 目的：把四路研究 + 綜合 + roadmap + 軸線決策落成文件，防 session 中斷遺失，作為後續開發依據。
> 📌 閱讀須知：**正文為最新定案**；過程中被取代/已更正的判斷（本地 ONNX/t3.small、約束「非官方」誤判）全部移到文末〈附錄 A 誠實軌跡〉，保留追溯但不誤導。

---

## 0. 重新分問（承認原分問錯誤）

**原分問（錯，複雜度不足/自滿）**：「12 個 backlog issue 挑哪個做？」→ 得「非阻擋高價值已耗盡→待命」。
**錯在哪**：量的是「demo 跑得動、畫面有說服力」，不是「**核心引擎是不是世界級**」。

**重新分問（對）**：TrustForge 唯一能打世界第一的 = **Trust Layer（信任提煉層）**。用「世界級信任引擎」的尺去量，它目前是 **heuristic / demo 級**——正好是我們宣稱的護城河，卻做得最淺。gap 大且具體，不是耗盡。

---

## 1. 四路研究（附據）

### 1A. 學術 SOTA（信任評分/claim verification）
- Claim verification：FEVER(2018)、AVeriTeC(2023/24)、RAFTS(2024，內建對抗論證)、HerO、AIC CTU@FEVER8(2025)。https://fever.ai/ ｜ https://arxiv.org/pdf/2410.23850
- Truth discovery（來源可靠度×事實真偽互相強化）：TruthFinder(2008)、CRH(2014)、CATD(2014)。survey https://www.kdd.org/exploration_files/Article1_17_2.pdf
- 跨源佐證超越 token overlap：NLI/entailment、stance detection、claim clustering。https://arxiv.org/html/2505.08464v1
- 操縱/協同造假：Cresci cashtag piggybacking(2019)、La Morgia crypto pump-and-dump(2021)、coordination-graph survey(2024)。https://arxiv.org/pdf/2105.00733
- 校準：Conformal Language Modeling(ICLR2024)、Conformal Abstention(2024)、ConU(EMNLP2024)。https://arxiv.org/html/2503.15850v1

### 1B. crypto 大廠 teardown
Messari(AI 全源引註研究)、Nansen(Smart Money/Token God Mode)、Arkham(實體標註+Intel Exchange)、Santiment(CryptoBERT 情緒+dev activity)、Kaito(Smart Followers/mindshare/Attention Markets)、Token Terminal(協議財報化)、LunarCrush(Galaxy Score/AltRank)、Glassnode(可回測 point-in-time)、Dune(SQL 儀表板)。
**結論**：全是**單一維度儀表板 + 黑箱分數**（只給分不說為什麼）。**沒有一家**做「逐主張可信度＋跨源驗證＋反方證據＋可解釋」。空白 = **「情報的情報」(intelligence-about-intelligence)**。打不贏數據規模，但這塊沒人做。

### 1C. 信任/溯源 UX 大廠 teardown
Ground News(bias/blindspot 光譜)、NewsGuard(9 準則營養標籤)、Perplexity(行內引用 UX)、**X Community Notes(bridging 共識演算法，開源，只獎勵跨立場都認同)**、Full Fact/ClaimReview schema、Snopes、AllSides(媒體光譜)、Kialo(論證樹)。
連結：https://ground.news/blindspot ｜ https://www.newsguardtech.com/ratings/rating-process-criteria/ ｜ https://jonathanwarden.com/understanding-community-notes/
**軸線答案**：這領域**世界級靠「可解釋的使用者友善」贏，不是視覺精緻**。NewsGuard/Community Notes/Full Fact 畫面樸素但都「一步步可驗證、可回溯判斷依據」。Ground News 被嫌 UI「drab」仍是第一品牌。

### 1D. gray issue triage（實測佐證）
- #5：`bedrock.py` **三個呼叫點(Step1/3/4)全無 timeout**，威脅 15 分鐘硬約束（真 robustness 缺口）。
- **#15 實測仍有真缺口**：DOMAIN_STOP 只擋中文域詞，**英文通用詞沒擋**，兩則語意相反英文主張仍 corr=0.5 → **獨立印證 1A 的 NLI 缺口**。
- #24 = 紅線 wontfix（官方資料下不觸發，硬做＝造資料）。

---

## 2. gap 表（核心引擎 vs SOTA，全 HIGH）

| 元件 | 現況(heuristic) | SOTA | gap | 佐證 |
|---|---|---|---|---|
| 跨源佐證 | token 重疊+停用詞+方向閘 | NLI/蘊涵/stance | **HIGH** | #15 英文 corr=0.5 |
| 來源信譽 | 人工固定權重 0.95/mid/0.35 | Truth-Discovery(TruthFinder/CRH)動態互估 | **HIGH** | 靜態無法適應 |
| 操縱偵測 | regex/關鍵詞 | 協同行為圖(pump-and-dump) | **HIGH** | 換詞即繞過 |
| 判定信心 | 無(裸加權+硬門檻0.5) | conformal 校準+abstain | MED-HIGH | 門檻武斷 |

---

## 3. 軸線決策（老闆 2026-07-01 拍板）

- **押「演算法深度 × 可解釋性」這條合一的軸**（演算法夠深＋能解釋為什麼＝可信＝友善），視覺美術第三順位。
- 不是「使用者友善 vs UI/UX 美術」二選一。現有「事實→推論→結論」方向對，缺**深度**與**可探索性**，不是美工。

---

## 4. 世界第一 roadmap（研究有據、7/13 前）

- **Tier 1 護城河深化**：
  - **W1 語意佐證**（取代 token 重疊，修 #15 真洞）← 2b 純 stdlib 反義閘**已 revert**(3 輪反覆錯殺佐證,詳決策日誌)；**改直接做 W1.5 = Bedrock Haiku 語意 stance 子分類器**(§5.5,唯一合規且正確路徑)
  - W2 truth-discovery 動態來源信譽（TruthFinder/CRH，用現有溯源歷史）
  - W3 bridging 共識/抗操縱（Community Notes 精神，獎勵跨立場認同）
  - W4 conformal 校準 + abstain 帶（取代硬門檻 0.5）
- **Tier 2 可解釋 UX**：跨源分歧條(Ground News blindspot)｜逐項 why(NewsGuard 營養標籤)｜來源立場/獨立性標籤
- **Tier 3 穩健收尾**：#5 Bedrock timeout｜清殘枝分支(10 個已合併 squash)｜關 #15(做完W1)/#24(wontfix)

---

## 5. 對照黑客組評分重點的誠實體檢（2026-07-01）

黑客組強調：技術深度、創意可用性、**具備使用企業資料的能力（運用命題文件及資料）**、可佈署實證之現場展示。

| 評分重點 | 現況誠實評 | 距「世界第一/沒人比得上」 |
|---|---|---|
| **技術深度** | 三層架構 + 反作弊設計(判斷本地產生)是加分；但**核心演算法 heuristic**(token重疊/固定權重/regex/無校準)，4 核心元件對 SOTA 全 HIGH gap | ❌ **未達**。「有架構、無深度」。W1-W4 才是真深度 |
| **創意可用性** | 定位創意夠——「情報的情報」是所有大廠的空白，無人做逐主張可解釋信任 | ⚠️ **概念贏、執行未兌現**。站在大廠空白，但呈現/演算法尚淺；W1+bridging+分歧視覺化才兌現 |
| **企業資料能力** | 官方 OHLCV 命題資料**已整合**(rep 0.95 納入評分)；hoyabit 連接器介面就緒但**僅 sample**，真企業數據卡 **7/13 工作坊** | ⏳ **部分/卡日期**。佔分最大(30%+20%)，7/13 前無法全做，介面先備妥 |
| **可佈署實證現場展示** | EC2 live（公開位址已去識別）、真 Bedrock 接通、15 分鐘壓測 25-68s(13× 餘裕)、冪等部署腳本、AWS 架構圖對齊真實部署 | ✅ **相對強項**。但預設離線(credit 安全)、核心引擎淺——「展示得動，展示的內容還不夠深」 |

**綜合誠實結論**：
- **技術深度沒人比得上？** 沒有。目前是 demo 級 heuristic 核心，這正是老闆重新分問戳中的點。W1-W4 是把「宣稱的護城河」做成「真的護城河」。
- **創意超越大廠？** 概念上站在他們沒做的空白（可解釋逐主張信任），但**執行還沒把創意變成不可辯駁的展示**。
- **強在哪**：可佈署實證/重現性、定位/概念創意。**弱在哪**：技術深度（核心引擎淺）。**卡在哪**：企業資料（7/13）。
- W1-W4 roadmap 直接攻「技術深度」這個最大弱點 → 驗證老闆重新分問方向正確。

---

## 5.5 W1.5 路線（已定案，2026-07-01）

**官方硬約束（錄取信/決賽須知白紙黑字）**：**「僅限使用 AWS 服務提供之基礎模型。」** → 語意判斷用的模型必須是 AWS 提供的 FM。

**定案**：
- ❌ 本地 ONNX mDeBERTa（非 AWS 提供模型）**出局**。
- ✅ **W1.5 = Bedrock Haiku 逐對 stance 子分類器**（`apac.anthropic.claude-haiku-4-5`）：AWS FM 合規、**<$0.01/次、15 分內、零新依賴、無 RAM 問題（不需 t3.small）**、與現有 `bedrock.py` 同路徑。
- **確定性**：結構化 enum 輸出（entailment/contradiction/neutral）+ 逐對結果快取 → 保證可重複（非靠模型）。
- **反作弊**：stance 標籤只是餵進我方確定性公式的**一個特徵**，最終 TrustScore/市場判斷仍我方 pipeline 組合 → **合規**（非把第三方現成結論當主結果）。7/13 工作坊跟窗口 Mars Li 一句 double-check。
- **W1（2b 純 stdlib、不使用任何基礎模型）不受此約束影響**，續走 merge。

> 本節取代早前的「本地 ONNX / t3.small」可行性分析與「約束非官方」誤判——完整過程與教訓見文末〈附錄 A〉。

---

## 6. 決策日誌
- 2026-07-01：老闆重新分問，否定「工作耗盡」的自滿結論；要求讀論文/研究大廠。
- 2026-07-01：四路研究完成；CEO 綜合；老闆拍板 **軸線=演算法深度×可解釋**、**起手 W1 語意佐證**、**節奏一次一項 CEO 審完再下一項**。
- 執行 SOP：gray(CPO)計劃→CEO審→CTO(sonnet)背景 feat 分支+PR→eye+codex→CEO 親測→merge→回報。副手不可信，CEO 一定親測。
- 2026-07-01：老闆追加把關指令——語意模型評估須量 Peak RSS(非只延遲)、防語境反轉(雙重否定 parity、領域漂移、判不準回 neutral 寧漏抓不誤判)。已轉達 CTO/研究員。
- 2026-07-01：W1 2b CTO 交付 PR #25（新 stance.py + #15 0.5→0）。**CEO 親測抓到真 bug**：中文單字「不」漏偵測 →「不明確」湊假 contradict、錯殺合法佐證 → 退回 CTO 修（加單字不/沒 + 濾不僅/不斷 + 3 回歸測試）→ 複驗 214 綠 + 8 探針全過、殘留失敗安全。印證副手測試綠仍需 CEO 親測。
- 2026-07-01：**官方錄取信確認硬約束「僅限使用 AWS 服務提供之基礎模型」**→ 本地 ONNX 出局、**W1.5 定案 Bedrock Haiku stance 分類器**（§5.5）。更正了先前「約束非官方」誤判（詳附錄 A）。
- 2026-07-01：**W1 2b 三輪後 revert（老闆拍板）**。round1 CEO 親測抓單字「不」；round2 Claude code-review 抓一叢 5 個(子字串/否定窗/全occurrence/清空token回歸/半成品evidence)；round3 **codex 抓「≥2反義對規則被跨子句共現打敗」+「空token fallback 虛構佐證」**。結論：詞表 heuristic 做語意矛盾判斷本質脆弱、反覆冒本模組自認最糟的「錯殺合法佐證」，療法比原病(#15 假佐證)更傷。**關 PR #25、#15 留 backlog 標記 W1.5、main 未污染**。改由 W1.5 Bedrock Haiku 語意 stance 做對。
- 教訓：**eye + 我的親測 + Claude code-review 都有各自盲點；獨立第二模型(codex)抓到我親自設計的 ≥2對規則與 fallback 的漏洞——SOP「eye + codex」缺一不可，尤其 codex 這關**。

---

## 附錄 A — 已被取代/已更正的判斷（誠實軌跡，勿當現行結論）

> 保留這段是為了誠實與可追溯：記錄我們一度採信、後被更正的判斷，以及教訓。**現行定案一律以正文 §5.5 為準。**

### A-1. 舊 NLI 可行性 spike（本地 ONNX/t3.small）——已被 §5.5 取代
當時（誤以為可用本地模型）評估：`deberta-v3-xsmall/small` 僅英文詞表，專案中英混雜 → 須多語 mDeBERTa（int8 ~317MB、Peak RSS 外推 ~700MB-1.2GB）→ t3.micro(1GB) 不可行、需 t3.small(2GB, +$9-11/月)；多語 317MB > Lambda 250MB → 只能 EC2/container；Peak RSS 為引用外推非實測。老闆一度原則預批 t3.small。
**為何取代**：官方「僅限 AWS 服務提供之基礎模型」→ 本地 ONNX（非 AWS 模型）出局，整段 t3.small/RAM 分析作廢。改用 Bedrock Haiku 後 RAM 問題根本消失。

### A-2. 「約束非官方」誤判 → 更正（重要教訓）
- **誤判**：CEO+研究員 grep 我方 `COMPETITION.md`（自稱唯一權威依據）**查無**「僅限 AWS 基礎模型」，一度結論「這只是我方自我約束、非官方鐵律」，並據此認為本地 ONNX 也許可行。
- **更正**：老闆轉來**官方錄取信/決賽須知**，白紙黑字「**僅限使用 AWS 服務提供之基礎模型**」——**是官方鐵律**。
- **錯因**：`COMPETITION.md` 是我方自製摘要且**漏收此條**；只查它 → 誤判。
- **教訓（沉澱）**：**官方原文（錄取信/活動附則）才是權威，自製摘要不可當唯一依據**。查證前先確認來源是否完整。COMPETITION.md 待在 main 補回此條（勿混進 W1 PR）。
- 附帶正確的部分（仍成立）：把 Bedrock 當「逐對 stance 子分類器、最終 TrustScore 我方公式組合」＝合規，非把第三方現成結論當主結果。
