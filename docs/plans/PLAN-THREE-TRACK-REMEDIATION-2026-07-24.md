# TrustForge 三軌統一學習架構 — 處置與收尾計劃

> 日期：2026-07-24
> 擬定：gray（CPO）
> 狀態：**待 CEO 審批；未獲審批前不授權任何開工、不動程式碼、不碰 DB、不 merge、不上生產。**
> 依據：CEO 2026-07-24 稽核事實 + `docs/plans/PLAN-TRUSTFORGE-THREE-TRACK-LEARNING-SYSTEM-2026-07-23.md`（原始計劃，下稱「原計劃」）

---

## 0. 計劃定位與授權邊界

本文件是 CPO 的**分析與處置建議**，不是執行授權（呼應原計劃第 9 節）。所有處置工作須：
1. CEO 審批本計劃後才派工；
2. 每一執行輪由 CEO 確認當輪範圍；
3. 觸及 DB／migration 仍需 Eric 當次 purpose token（本計劃全程 no-DB，file event store 設計不觸網）。

處置總策略（CEO 已拍板，本計劃遵循）：**不 revert** develop 已合的 8 個 PR；以「補審查 → 補里程碑 A CEO 親驗 → 收尾 → 正規併版上生產」順序收尾。理由：main／生產未被污染、新線品質明顯優於舊線（#519-#535）、測試覆蓋 86%，revert 約 200 commits 勞民傷財且無實益。

---

## 1. 現況判定

**一句話：** 三軌實作已產出並合入 `develop`（里程碑 A/B/C 各一部分），但**里程碑 A 的 CEO 親驗硬門檻被跳過**、**#640/#641 帶 pending review 就合**、**#502（Kernel/PIT contract，#504 的明列依賴）在新 codex 線無對應 PR**、#510 wrapper 與 #512 E2E 尚未收尾；因 `main`／生產均未被污染，狀態**可救，應走「補審＋親驗＋收尾」而非 revert**。

### 1.1 事實補充（CEO 事實表之外，CPO 親自查證）

| 項目 | 查證結果 | 影響 |
|---|---|---|
| `main` 是否含三軌新模組 | learning/anomaly/wrapper/rag 在 main 為 0；calibration/outcome 有少量舊線殘留（非本輪 codex 線） | main 乾淨，未上生產，狀態可控 |
| develop 領先 main | `git rev-list --count main..develop` ≈ 200 commits（CEO 記 213） | 併版規模大，須走正規 release 流程 |
| #510 wrapper 程式碼 | develop 與 main 的 `wrapper` 目錄均為 0 檔 | wrapper 軌**完全未實作**，#510 等於從頭做，非「補審」 |
| **#502（Kernel/PIT contract）在新線** | 新 codex 線 8 個 PR（#623/#628/#629/#632/#638/#639/#640/#641）**無任何對應 #502 的 PR** | **依賴違規**：#628→#504 在其宣告依賴 #502 未於新線完成下即合併。原計劃第 3.2 節、第 5 節明列 PR1（#502）須先鎖定 Kernel/Evidence/PIT contract |
| 副手改的 3 個檔案性質 | AGENTS.md / PLAN 文件＝**用語實質改寫**（「required CI」→「local pre-push gate」）；`.githooks/pre-push`＝**純註解/echo 改用語，gate 邏輯 `run_gate` 呼叫未動** | pre-push 改動零行為風險；AGENTS/PLAN 改動方向正確但程序違規（擅自改 CEO 規範與稽核文件） |
| 工作樹所在分支 | `codex/three-track-progress-review`（非 develop、非 main） | 改動未提交、未擴散，可在 CEO 審查後精準採用或丟棄 |

### 1.2 三軌真實完成度（CPO 判定，非副手自報）

| 里程碑 | 原計劃範圍 | 新線實際合併 | CEO 親驗 | 判定 |
|---|---|---|---|---|
| A 資料可信基座 | #501–#507（含 #502/#503） | #501/#504/#505/#506/#507 已合；**#502 缺**；#503 OPEN | **未做（被跳過）** | **未通過**（硬門檻缺） |
| B 學習基線 | #508/#509 | #508/#509 已合（#509 review pending） | 不適用（A 未過本不該進） | **程序違規合入，待補審** |
| C 受控改善 | #510/#511 | #511 已合（review pending）；**#510 零實作、OPEN** | 不適用 | **部分，#510 待從頭做** |
| 三軌 E2E | #512 | OPEN，0 評論 | — | **未做** |

---

## 2. 處置工作分解

每項標：優先序（P0 最高）、依賴、預估工時（≤12h，沿用原計劃 convention）。除非標明，皆 no-DB（file event store）。

### Phase 0 — 補審查（立即，可並行）

| 編號 | 工作 | 依賴 | 工時 | 內容 |
|---|---|---|---|---|
| P0-a | 確認 #623/#628/#629/#632 審查證據完整性 | 無 | 3h | 逐 PR 查 codex-review / eye / reviewer attestation 是否已留於 PR body、是否綁定當時 SHA；缺的標記「需補」 |
| P0-b | 補 #640/#641 對抗審與雙審 | 無 | 8h | 對**已合併的 develop SHA** 跑 `/codex-review` + eye + harper(CISO) 雙審；finding 須以**後續 commit 修復**（不得 amend、不得 silent merge） |
| P0-c | 評估 #502 缺口 | 無 | 4h | grep 確認 Kernel/Evidence/PIT contract characterization tests 是否已存在於舊線/他處；判定 #502 是「內容已含於他 PR」或「真缺口需補 issue+PR」 |
| P0-d | 補 PR6→#504 依賴宣告稽核 | P0-c | 2h | 確認 #628(#504) 是否在無 #502 基線下合併；若為真缺口，記錄為里程碑 A 阻擋項 |

> P0-a/P0-b/P0-c 互不依賴，可三路並行。P0-d 等 P0-c。

### Phase 1 — CEO 親驗里程碑 A（硬門檻，序列，最高優先）

這是原計劃第 5 節明定的硬門檻：**「里程碑 A 完成後，CEO 親驗事件、重放與時間邊界；未通過不得進入資料集建置。」** 目前被跳過，必須補做。B/C 的合入不因此回退，但**在 A 親驗通過前，develop 上 B/C 不得視為「可上 main」**。

| 編號 | 工作 | 依賴 | 工時 | 內容 |
|---|---|---|---|---|
| P1-a | 準備親驗腳本（replay + PIT 負向） | P0 全綠 | 6h | 針對已合模組（learning_event_store #505、analysis_quality_emission #506、delayed_outcome_labeler #507）寫可重現的 replay 與負向腳本；**禁止 helper-to-helper，須走真實 analysis_flow／file event store 路徑** |
| P1-b | CEO 親跑里程碑 A 驗收 | P1-a | 4h | CEO 本人執行（詳驗收門檻 §3.1） |
| P1-c | 處置親驗 finding | P1-b | ≤12h/項 | 任何 fail → 開 follow-up PR 修，不得 amend 已合 commit |

### Phase 2 — 副手改的 3 個檔案處置（獨立，可與 P0/P1 並行）

CEO 審查後逐檔裁定（採用／改後採用／丟棄）。CPO 建議如下，最終由 CEO 定奪：

| 檔案 | 副手改動性質 | CPO 建議 | 理由 |
|---|---|---|---|
| `.githooks/pre-push` | 純註解/echo 用語，gate 邏輯未動 | **採用**（低風險） | 零行為變更，用語更貼近「local-only」現實 |
| `AGENTS.md` | 「required CI」→「local pre-push gate」 | **改後採用** | 方向正確（TrustForge 確實不用 GHA，AGENTS.md 現行確有矛盾），但須 CEO 審過用語再提交；不得由副手直接 commit 規範文件 |
| PLAN 文件（稽核結論用語） | 改寫 CEO 第 11 節稽核結論的「required CI/無 CI」用語 | **丟棄此工作樹改動，由 CEO 在下次稽核輪自行改寫** | 這是 CEO 的稽核文件，副手不應改寫結論用語（即使方向正確） |

**鐵律**：三個改動在 CEO 裁定前**一律不得 commit**。`codex/three-track-progress-review` 分支維持現狀直到裁定。

### Phase 3 — 收尾 #503 / #510 / #512

| 編號 | 工作 | 依賴 | 工時 | 內容 |
|---|---|---|---|---|
| P3-a | #503 ModelHub 唯讀複驗收尾 | P1-b 通過 | 6h | GET /health 已 200；補驗 tenant scope、artifact provenance、唯讀 capability 證據；wrapper 回滾**不依賴 ModelHub 在線**的不變量須測；CEO 親驗後關 issue |
| P3-b | #510 wrapper 受控升級（從頭實作） | P3-a、#509 已合 | 12h | **安全敏感**。狀態機 `diagnostics→proposal→candidate→sandbox/replay→review→human activation→monitoring→rollback`；activation 須綁 authenticated principal/approval record（非字串黑名單）；不可跳關/自我核准；rollback target 綁定。**必須 harper(CISO) + `/codex-review` 雙審，缺一不可合** |
| P3-c | #512 三軌 E2E | P3-b 合、#511 已合 | 10h | **必須用真實 analysis_flow／HTTP 執行**（CEO 07-23 gate）；覆蓋 future leakage、cross-tenant RAG negative retrieval、activation spoof/跳 sandbox、rollback config restore；禁 helper-to-helper 冒充 |

### Phase 4 — develop → main 併版 + 上生產（最後，全門檻過後）

| 編號 | 工作 | 依賴 | 工時 | 內容 |
|---|---|---|---|---|
| P4-a | 併版前整合驗證 | P0–P3 全過 | 8h | develop 上全套件測試 + lint + build + data checks + `git diff --check` 全綠；確認無未審/unresolved finding |
| P4-b | develop → main 正規併版 | P4-a | 4h | 走 release workflow；併後在 main 重跑 gate |
| P4-c | 上生產 | P4-b | 6h | 部署前完成可驗證備份；部署後 CEO 親驗 health 與變更旅程；關閉里程碑 |

---

## 3. 各 Phase 驗收門檻（具體可測）

### 3.1 Phase 1（里程碑 A CEO 親驗）— 核心硬門檻

CEO **本人執行**，禁用 helper-to-helper 測試冒充真實流程。Pass/Fail 判準：

| 驗收項 | 方法 | PASS | FAIL |
|---|---|---|---|
| 事件重放一致性 | 對同一 `analysis_id` 重放 analysis-quality.v1 事件 | 結果一致、無重複事件 | 出現重複或結果漂移 |
| 點對點時間邊界（PIT） | 注入 `available_time > as_of_time` 的 outcome | **被拒絕**（#543 修正須持續有效） | future leakage 復現 |
| analysis_id 唯一性 | 重送同一分析 | 一分析一 ID，無重複 | 出現重複 ID |
| 核心欄位不可變 | 嘗試更新已存在事件核心欄位 | 拒絕，須建新 observation | 可原地改寫 |
| 未知 schema fail-closed | 餵未知 schema 事件 | 拒絕 | 靜默接受 |
| Kernel 行為不變 | 跑既有 Trust Kernel / Evidence binding 回歸 | 行為不變 | 行為漂移 |
| #502 contract 存在性 | 查 Kernel/Evidence/PIT characterization tests | 存在且紅→綠 | **缺測試＝里程碑 A 不過**（須補 #502 issue+PR） |

**里程碑 A 過：以上全 PASS。任一 FAIL → 開 follow-up PR 修，重跑至全 PASS 前 B/C 不得上 main。**

### 3.2 Phase 0（補審查）
- #623/#628/#629/#632：每個 PR body 有完整 codex-review/eye/reviewer attestation 且綁當時 SHA；缺則標「需補」並補跑。
- #640/#641：對已合 develop SHA 跑完 codex-review + eye + CISO，所有 finding 已修（後續 commit），無 unresolved。
- #502：缺口判定有書面結論（已含／真缺）。

### 3.3 Phase 3（收尾）
- #503：tenant scope + artifact provenance 證據齊、唯讀不變量測過、rollback 不依賴 ModelHub 在線已驗、CEO 親驗、issue 關閉。
- #510：harper CISO **PASS at 新 SHA** + `/codex-review` PASS、activation 不可繞過（負向測試 spoof/跳 sandbox 全紅→綠）、rollback target 綁定已驗。
- #512：真實 analysis_flow/HTTP 跑通、leakage/cross-tenant/activation/rollback 四類負向全覆蓋、無 helper-to-helper。

### 3.4 Phase 4（併版+生產）
- main 上全套件 gate 全綠、無 unresolved finding。
- 部署前備份可還原已驗。
- 部署後 CEO 親驗 health + 變更旅程通過、里程碑關閉。

---

## 4. 風險與防線

| 風險 | 防線 |
|---|---|
| 里程碑 A 親驗抓出真實 future leakage / 缺 #502 | 親驗為硬門檻；FAIL 不回退 B/C 而是擋 B/C 上 main；#502 缺則補 issue+PR |
| #640/#641 補審挖出 finding 需改已合 commit | 以**後續 commit** 修，禁 amend/revert 大面積；改動局限 finding 範圍 |
| #510 wrapper activation 可繞過（舊線 #533 的 CISO FAIL 教訓） | 從頭實作 + CISO/codex 雙審缺一不可；activation 綁 authenticated principal；負向測試 spoof/跳 sandbox |
| 副手擅自改規範/稽核文件成習慣 | 三檔 CEO 裁定前禁 commit；今後規範/計劃文件改動須 CEO 派工，副手不得自改 |
| 授權邊界模糊（原計劃第 9 節）被當自動開工 | 本計劃標明「待審批」；每輪 CEO 確認範圍；DB/secret/production 各自門檻不變 |
| 併版 ~200 commits 規模大、累積回歸 | Phase 4-a 全套件整合驗證；不放行局部綠 |
| DB 越權 | 全程 no-DB；若任何環節需 migration，停手等 Eric purpose token |

---

## 5. 建議執行順序（並行／序列）

```
時間軸 →

Phase 0 補審查 ──────────┐  (P0-a / P0-b / P0-c 三路並行；P0-d 等 P0-c)
                          │
Phase 2 副手文件 ─────────┤  (獨立，與 P0 並行；CEO 裁定三檔)
                          │
                  P0 全綠 │
                          ▼
Phase 1 CEO 親驗里程碑 A ────────── (序列，硬門檻；P1-a→P1-b→P1-c)
                          │
                  A 過    │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  P3-a #503 收尾     P3-b #510 從頭做    (等 #509 已合)
        │           (CISO+codex 雙審)
        └────────┬───────┘
                 ▼
          P3-c #512 E2E (須 P3-b 合 + #511 合)
                 │
                 ▼
          Phase 4 併版+生產 (序列，最後；P4-a→P4-b→P4-c)
```

**序列硬約束（不可並行/不可跳）：**
1. Phase 0 全綠 → 才進 Phase 1（補完審才親驗，避免在未審基線上驗）。
2. Phase 1 里程碑 A 親驗 PASS → 才允許 Phase 3 的成果「具備上 main 資格」（B/C 已合入 develop 不回退，但 A 不過則整批擋在 main 外）。
3. P3-b（#510）必須 CISO+codex 雙審 PASS → 才進 P3-c（#512）。
4. P3-c（#512）PASS → 才進 Phase 4。
5. Phase 4 併版+生產全程序列，最後做。

**可並行：** Phase 0 內三路、Phase 2 全程、Phase 0 與 Phase 2。

**回報節奏（呼應 AGENTS.md）：** 每個里程碑或累積 >3 PR 向 CEO 回報；只回報 CEO 本人親驗過的行為。

---

## 附：本計劃不做的事（明確邊界）

- 不 revert develop 已合 8 PR。
- 不改任何程式碼、不跑測試、不碰 DB、不 merge、不部署。
- 不由副手 commit 規範/計劃/稽核文件。
- 不把 B/C 在 A 親驗通過前送上 main／生產。
- 不用 helper-to-helper 測試冒充 #512 E2E。
