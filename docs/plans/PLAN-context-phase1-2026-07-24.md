# AI Agent 新手脈絡功能 — Phase 1 開發計劃（#574 + #575）

> 日期：2026-07-24
> 依據：`PLAN-AI-AGENT-CONTEXT-FEATURES-2026-07-23.md`（A/G/P/E/Q 拆解與相依圖）、
> `AI-AGENT-CONTEXT-FEASIBILITY-2026-07-23.md`（資料模型可行性）
> 狀態：待 CEO 核准 — **本文件不含任何程式碼/測試/設定變更，僅規劃**
> 範圍：僅 #574（AssetContext schema）與 #575（glossary catalog），不擴及
> P/E 線或 UI annotation engine 之後續 issue

---

## 0. 關鍵發現（動工前盤點，先讀後寫）

在依照原計畫展開「Phase 1 = #574 + #575 net-new 實作」之前，盤點 in-flight PR 與
`develop` 分支後發現：**#574 與 #575 的驗收條件已經在 `develop` 分支上由既有
PR 完成，尚未回填/關閉 issue，也尚未合併回 `main`。** 這改變了 Phase 1 的
性質：從「新開發」變成「驗證、缺口盤點、issue 治理收斂」。以下逐項說明。

### 0.1 `main` 與 `develop` 已分岔

- 目前 repo 檢出於 `main`，HEAD 為 `159ebdd`（PR #635，#565 baseline 解鎖）。
- Feature 開發實際上都以 `develop` 為 base（見 `gh pr list --state open`，
  三張 open PR #642/#627/#618 的 `baseRefName` 均為 `develop`）。
- `main..origin/develop` 有 **171 commits**（含 #574/#575/#578/#579 全部工作），
  `origin/develop..main` 有 **20 commits**（含 #565 baseline pre-push 修復，
  `commit 159ebdd` 不在 `develop` 內）。
- 結論：**`develop` 尚未拿到 #565 的 baseline gate 修復**，`main` 也還沒有
  AssetContext/glossary 系列工作。這是分支治理風險，已提列為需 CEO/CTO 裁示
  項（見 §5），本計畫不擅自處理。

### 0.2 #574 驗收條件已由 PR #599 於 `develop` 達成

- PR #599（`feat/574-asset-context-schema-clean` → `develop`，**MERGED**）：
  新增 `src/trustforge/asset_context.py`（`AssetContext` frozen dataclass、
  `AssetSector`/`AssetLayer`/`TokenRole`/`MarketCapTier` 受控 enum，未知值一律
  顯式 `unknown`，不可留空/猜測）、`contract_schemas()` 掛載 JSON Schema、
  `docs/contracts/trustforge-data-contracts-v1.json` 已回填、9 項 round-trip /
  invalid enum / missing/blank / extra-fields / constructor enum 測試通過。
- **但實作欄位集與 #574 issue 原文不同**（見 §0.4 缺口）。

### 0.3 #575 驗收條件已由 PR #602 於 `develop` 達成

- PR #602（`feat/575-glossary-catalog-20260723` → `develop`，**MERGED**，
  PR 本文明寫 `Closes #575`）：新增 `src/trustforge/glossary.py`，單一
  `GlossaryTerm`/`GLOSSARY_CATALOG`、`GlossaryAudience`（report/popover/
  help_center 共用同一份資料）、`validate_glossary_catalog()` 擋重複
  `term_id`/alias、`CORE_GLOSSARY_TERMS` 已收錄 FDV、MC（market_cap）、TVL、
  Tokenomics、Gas Fee、解鎖賣壓（`unlock_sell_pressure`）六個核心詞彙，含中英文
  alias。`tests/test_glossary_catalog.py` 覆蓋重複 term_id/alias。
- 下游 #578（term annotation engine）已由 PR #607 合併；#579（Analyze API 掛
  `asset_context`/`risk_notices`）已由 PR #617 合併；#588（`AnnotatedText` UI）
  即為現正 open 的 PR #627，已獲 `cancleeric` approve，尚未合併。

### 0.4 欄位缺口：AssetContext 已交付內容 vs #574 issue 原文

| #574 acceptance criteria 提及 | develop 已交付（`asset_context.py`） | 差異 |
|---|---|---|
| `sector` | `AssetSector` enum（含 unknown） | 一致 |
| `layer` | `AssetLayer` enum（含 unknown） | 一致 |
| `settlement_chain` | **無此欄位** | 缺 |
| `gas_token` | **無此欄位** | 缺 |
| `token_roles`（複數/陣列） | `token_role`（**單數，單一 enum**） | 語意窄化：一個資產只能標一個角色 |
| `dependencies` | **無此欄位** | 缺（改由後續 E-line `DependencyEdge` 承接，見原計畫 §5 E1） |
| （原文未提及） | 新增 `market_cap_tier`、`ecosystem`、`parent_asset_id`、`tags` | 實作擴充但 issue 未載明 |

判斷：`settlement_chain`/`gas_token`/`dependencies` 在原始可行性文件中屬於
「Layer 上下游卡」的展示欄位，但正式合併時被收斂到更保守的核心分類 schema，
依賴關係被有意延後到 E-line（`DependencyEdge`）獨立建模——這是合理的 scope
收斂，但**#574 issue 文字本身沒有更新，形式上驗收條件對不上已交付程式碼**。
`token_roles` 從複數變單數則是語意限縮（一個資產只能一種角色），需要產品確認
是否可接受（例如同時具治理與 Gas 用途的資產無法完整表達）。

**這是需要 CEO 裁決的分岔點**（見 §5 決策清單），不是可以由本計畫單方面
決定的事，故 Phase 1 兩條路徑（§1.2）都先寫出，等裁示後才執行。

---

## 1. 目標與非目標

### 1.1 目標

1. 確認 #574、#575 的既有交付物（`develop` 上的 PR #599、#602 及下游
   #607/#617）是否足以視為 Phase 1 完成，並提出正式關閉 issue 的條件。
2. 若欄位缺口（§0.4）被判定為必須修補，規劃一張**小型、範圍受限**的
   delta PR（非重造），把 `settlement_chain`/`gas_token` 或 `token_roles`
   複數化的落差補齊，且不影響已合併程式碼的相容性。
3. 規劃驗證動作（跑既有測試、對照 OpenAPI/contract 產物），確保
   `develop` 上的 #574/#575 相關程式碼在目前 HEAD 仍是綠的，作為關閉
   issue 前的證據。
4. 盤點與 in-flight PR（#627、#618）的協調點，避免關閉 issue 或補丁 PR
   與它們打架。
5. 提出 `main`/`develop` 分岔的處理建議（不執行，僅提請 CEO/CTO 裁示）。

### 1.2 非目標（明確排除，屬 Phase 2 之後）

- 不做 P 線（PeerMetricsSnapshot/TVL/TPS connector）——`#618` 是既有
  TVL connector 的安全跟進修，屬既有 in-flight 工作，本計畫僅盤點協調點，
  不納入 Phase 1 交付範圍。
- 不做 E 線（DependencyEdge/UpgradeEvent/ImpactPath）。
- 不做 UI 卡片（`SectorLayerCard`）或 annotation engine 前端整合——`#588`
  對應的 `AnnotatedText` 已是獨立 in-flight PR #627，屬 G-line 下一棒，非
  本次 #574/#575 範圍。
- 不處理 `main`/`develop` 分岔本身的合併/rebase 操作——僅在 §5 提出決策
  請求，執行需 CTO/COO 排程。
- 不修改任何程式碼、測試、設定；本文件產出後若裁示需要補丁 PR，由對應
  工程師依本計畫另行建立分支與 PR。

---

## 2. 與 in-flight PR 的協調點

| PR | Base | 狀態 | 與 #574/#575 的關係 | 協調結論 |
|---|---|---|---|---|
| #627 `feat(ui): add annotated glossary text` | `develop` | Open，已 approve，未合併 | 直接消費 `glossary.py`/`GLOSSARY_CATALOG`（經 frontend `glossaryCatalog.ts` 鏡射）；改動 `frontend/src/lib/annotatedText.ts`、`GlossaryTerm.tsx`、`AnalysisReportView.tsx`、`FactsInferenceLadder.tsx`、`KeyBasisList.tsx`、`index.css` | **無檔案衝突**：#575 的後端 catalog（`src/trustforge/glossary.py`）已定案且 #627 是純前端消費端，未修改 `glossary.py`。若 §0.4 決議要調整 glossary schema（本計畫未提案調整，僅 AssetContext 有缺口），必須先確認不影響 #627 已假設的 term 結構（`term_id`/`label`/`aliases`/`definition`）。**建議：#575 不再變動，維持現狀，讓 #627 先合併**，不要因為 Phase 1 收斂動作延後它。 |
| #618 `fix(metrics): enforce safe fetch for TVL connector` | `develop` | Open，已 approve 待 review gate（Codex 對抗性審查） | 改動 `src/trustforge/tvl_connector.py`、`tests/test_tvl_connector.py`，屬 P-line（#581），與 #574（`asset_context.py`）、#575（`glossary.py`）**檔案完全不重疊** | 無需協調，純粹並行；本計畫僅記錄以證明已盤點，不納入 Phase 1 動作項。 |

---

## 3. 資料契約（沿用既有規劃並標明落差）

### 3.1 AssetContext（已於 `develop` 交付，`src/trustforge/asset_context.py`）

```python
schema_version: str = "1.0.0"
asset_id: str
symbol: str
name: str
sector: AssetSector            # enum，含 unknown
layer: AssetLayer               # enum，含 unknown
token_role: TokenRole            # 單一 enum，含 unknown（原文為複數 token_roles）
market_cap_tier: MarketCapTier   # enum，含 unknown（原文未列，屬交付擴充）
ecosystem: str | None = None     # 原文未列，屬交付擴充
parent_asset_id: str | None = None  # 原文未列，屬交付擴充
tags: tuple[str, ...] = ()        # 原文未列，屬交付擴充
```

- 缺：`settlement_chain`、`gas_token`、`dependencies`（原文列於 #574，已交付未含）。
- Provenance：實際存放與有效期間由 `asset_context_repository.py`
  （對應 A2，已於 `develop` 存在但**不屬本 Phase 1 範圍**，僅記錄以便
  理解落點）承載 `sources`/`valid_from`/`fetched_at`，不在 `AssetContext`
  本體內——與原計畫「AssetContext 內含 sources/valid_from/fetched_at」
  的設計不同，改為 repository 層外掛。這也是需要 CEO 確認的設計差異
  （是否接受 provenance 與分類分離存放）。

### 3.2 GlossaryEntry（已於 `develop` 交付，`src/trustforge/glossary.py`）

```python
term_id: str
label: str
definition: str
aliases: tuple[str, ...] = ()
audiences: tuple[GlossaryAudience, ...] = (REPORT, POPOVER, HELP_CENTER)
```

- 與原計畫 `GlossaryEntry`（`canonical_label`/`plain_language_definition`/
  `risk_note`/`references`/`version`）欄位命名不同，但語意涵蓋：
  `label`≈`canonical_label`、`definition`≈`plain_language_definition`。
  **缺 `risk_note`、`references`**（原計畫要求的風險提示與引用來源，#575
  交付版本未含）。若 Phase 1 要嚴格符合原計畫的「風險提示」用途，需追加
  欄位；但六個核心詞彙目前的 `definition` 已包含足夠脈絡（例如「解鎖賣壓」
  定義本身即帶風險語意），**是否仍需獨立 `risk_note` 欄位屬產品措辭裁量，
  提交 CEO 裁示（見 §5）**。
- Catalog 版本化以模組級 `GLOSSARY_CATALOG_VERSION = "1.0.0"` 呈現，未走
  單筆 `version` 欄位，屬合理簡化。

---

## 4. PR 拆解（依裁示結果分兩條路徑，皆為小工時）

### 路徑 A（建議）：純驗證與治理收斂，不寫新程式碼

| # | 標題 | 內容 | 檔案 | 驗收 | 工時 | Reviewer | 可平行 |
|---|---|---|---|---|---:|---|---|
| A-1 | `chore(#574,#575): 驗證 develop 既有交付並回填 issue` | 在 `develop` HEAD 執行 `pytest tests/test_asset_context_contract.py tests/test_glossary_catalog.py tests/test_asset_context_repository.py -q`、`python3 scripts/check_data_contracts.py`，把結果與 §0.4 欄位落差整理成 issue comment，附驗收條件逐項打勾/註記例外 | 無程式碼變更；僅 GitHub issue 留言 + 本計畫文件 | 兩張 issue 均有對應 PR 連結與測試證據；欄位落差已列為已知例外或另開 follow-up issue | 1h | product-manager（issue 治理）、qa-lead（測試證據覆核） | 可與 A-2 平行 |
| A-2 | `docs: 更新資料契約文件反映交付欄位` | 若裁示接受現狀，更新 `docs/README.md` 索引指向本計畫；若既有「資料契約架構文件」有提到 `settlement_chain`/`gas_token`/`token_roles`（複數），需標註「已改為 §3.1 現況，設計差異見本計畫 §0.4」 | 僅文件（`docs/**`），不觸碰 `src/**`/`tests/**` | git diff --check 通過；文件連結有效 | 1h | product-manager | 可與 A-1 平行 |

路徑 A 總工時：**2h**，全部平行，wall-clock 約 1h。**不產生新的 code PR**，
Phase 1 視為「以既有 `develop` 交付 + 記錄已知落差」關閉。

### 路徑 B（僅在 CEO 裁示「必須補欄位」時才開工）

| # | 標題 | 內容 | 檔案 | 驗收 | 工時 | Reviewer | 可平行 |
|---|---|---|---|---|---:|---|---|
| B-1 | `feat(context): AssetContext 補 settlement_chain/gas_token 選填欄位` | 在既有 `AssetContext` frozen dataclass 新增兩個**選填**（預設 `unknown`）欄位，不改動既有必要欄位順序與既有測試斷言之外的部分，向下相容既有序列化資料 | `src/trustforge/asset_context.py`、`tests/test_asset_context_contract.py`、`docs/contracts/trustforge-data-contracts-v1.json`（regenerate） | round-trip/invalid enum/missing 測試更新；既有 9 項測試不回歸；`check_data_contracts.py` 綠 | 4h | CTO 指派工程師 + qa-lead | 與 B-2 可平行（不同欄位邏輯，唯一交集是 dataclass 定義需序列化合併，建議同一人做以免衝突） |
| B-2 | `feat(context): token_role 改為 token_roles 陣列（breaking，需相容層）` | 因涉及既有已合併 API（#617 已把 `asset_context` 掛到 analyze payload）之欄位型別變更，屬**對外契約 breaking change**，需先評估是否用新增 `token_roles` 陣列欄位並列共存（`token_role` 保留供舊消費端相容）取代直接改型別 | `src/trustforge/asset_context.py`、`src/trustforge/schema.py`（analyze payload）、`frontend/src/lib/types.ts`、對應測試 | 新舊欄位並存期驗收；契約相容性測試；OpenAPI 同步 | 8h（若 refinement 後超過需再拆） | CTO 指派工程師 + qa-lead + **需 CEO 額外核准**（見 §5，對外契約變更） | 依賴 B-1 定案後才排（同檔案，序列避免衝突） |
| B-3 | `feat(glossary): 補 risk_note/references 欄位` | 若裁示六詞彙需要獨立風險提示與引用來源 | `src/trustforge/glossary.py`、`tests/test_glossary_catalog.py`、frontend 對應型別（需先確認不影響 #627 已定案的 `glossaryCatalog.ts` 形狀，建議等 #627 合併後才做，避免與其正在 review 的 PR 打架） | 新欄位選填、既有 6 詞彙全部補齊、alias/重複測試不回歸 | 3h | product-manager（審措辭）+ qa-lead | 需在 #627 合併後才開工（序列） |

路徑 B 總工時：**15h**（若三張都做），B-1/B-3 可與彼此平行，B-2 需等 B-1
定案且**必須先取得 CEO 額外核准**才能排入（見 §5 對外契約變更）。

---

## 5. 需 CEO 裁示的點（本計畫不擅自決定）

1. **`main`/`develop` 分岔**（§0.1）：171 commits 只在 `develop`、20 commits
   只在 `main`（含 #565 baseline 修復）。是否要先做一次 `develop` ← `main`
   合併把 baseline gate 修復同步過去，再談 Phase 1 issue 關閉？這牽涉
   release 流程，建議轉給 CTO/COO 排程，但需 CEO 認可優先序。
2. **#574 欄位落差是否可接受為既成設計**（§0.4）：
   - `settlement_chain`/`gas_token`/`dependencies` 不補（依賴移交 E-line）—
     建議接受，因為原計畫本身就有獨立的 `DependencyEdge`（E1）用於依賴關係，
     這裡的收斂與長期架構一致。
   - `token_roles` 複數 → `token_role` 單數：**這個有實質語意限制**（無法
     同時標「治理 + Gas」），且已被 #617 掛到公開 API，屬對外契約，
     若要改需要 CEO 核准（路徑 B-2）並排 adversarial review。
3. **Glossary 是否需要獨立 `risk_note`/`references` 欄位**（§3.2、路徑
   B-3）：目前六詞彙定義已隱含風險語意，是否要求正式拆欄位屬產品措辭
   決策。
4. **是否直接視 Phase 1（#574/#575）為已完成並關閉 issue**，還是要等
   路徑 B 的任一張補丁 PR 合併後才關閉：建議前者（路徑 A），因為 B 線
   風險（尤其 B-2 breaking change）不應阻塞既有已上線功能的 issue 治理，
   可另開 follow-up issue 追蹤。

---

## 6. 執行順序與相依圖

```text
路徑 A（建議，本次 Phase 1 唯一交付）
  A-1 驗證既有測試與欄位落差 ─┐
  A-2 更新資料契約文件        ─┴─ 完成 → 回填/關閉 #574、#575

路徑 B（僅 CEO 裁示需要才排，且晚於 #627 合併）
  B-1 補 settlement_chain/gas_token（選填）
     └─ B-2 token_roles 陣列化（breaking，需 CEO 額外核准）
  B-3 glossary risk_note/references（需晚於 #627 合併）
```

- A-1、A-2 可完全平行，wall-clock 約 1h。
- B 線不計入本次 Phase 1 wall-clock，僅為裁示後備案。

---

## 7. 品質門檻

- 路徑 A：不涉及程式碼變更，門檻為「既有測試證據齊全 + issue 留言附連結」，
  由 qa-lead 覆核測試輸出，product-manager 確認 issue 驗收條件逐項對應。
- 路徑 B（若啟動）：
  - B-1：一般 reviewer + `/codex-review`，非對外契約 breaking，不需額外
    adversarial review。
  - B-2：**對外契約 breaking change**，除 reviewer + `/codex-review` 外，
    需 adversarial review，且需 CEO 額外核准才能排入（見 §5-2）。
  - B-3：涉及產品措辭（風險提示文案），product-manager 需審稿；因為
    是新增選填欄位不動既有契約，不需 adversarial review，但若最終文案
    涉及「保證/建議」用語需比照禁止投資建議的既有紅線覆核。

---

## 8. 開工條件

1. CEO 就 §5 四點裁示。
2. 若選路徑 B，需先確認 #627 已合併（避免 glossary 前端假設被打亂）。
3. `main`/`develop` 分岔處理排程確認（§5-1），避免 A-1 驗證用的 base
   commit 與未來合併後結果不一致。
