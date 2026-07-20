# Kiro 使用證據（+10% 加分項）

> 本文件證明 TrustForge Hermes 全程使用 AWS Kiro 進行開發，符合競賽加分條件。
>
> 產生時間：2026-07-20

---

## 1. `.kiro/` 目錄結構

```
.kiro/
├── steering/
│   ├── project.md              # 專案規範（架構/技術棧/慣例）
│   ├── competition.md          # 競賽約束（硬規則/交付物/評分權重）
│   └── trust-layer.md          # 信任層開發規範（評分公式/鐵則/測試要求）
├── hooks/
│   ├── lint-on-save.json       # 存檔時自動 lint
│   └── test-before-commit.json # commit 前自動跑測試
└── specs/
    ├── security-gate-205.md            # 安全閘門 spec
    ├── celebrity-whale-trades.md       # 名人/鯨魚交易追蹤 spec
    ├── issue-244-285-small-screen-layout.md  # 小螢幕佈局 spec
    ├── backfill-worker.md              # 回填 worker spec
    └── budget-governance-api.md        # 預算治理 API spec
```

---

## 2. Kiro 使用證據清單

### 2.1 Steering Files（專案規範管理）

| 檔案 | 用途 | 觸發條件 |
|------|------|----------|
| `project.md` | 定義三層架構、技術棧、幣種池、開發慣例 | 所有互動自動載入 |
| `competition.md` | 確保遵守競賽硬約束（Bedrock only、反作弊、15 分鐘限時） | 所有互動自動載入 |
| `trust-layer.md` | 信任評分公式、來源信譽表、開發鐵則 | 修改 `src/trustforge/trust/**` 時自動載入 |

**效果**：開發者與 AI 共享同一組規範，避免 AI 生成違反競賽規則的程式碼。

### 2.2 Hooks（自動化品質管控）

| 檔案 | 觸發時機 | 動作 |
|------|----------|------|
| `lint-on-save.json` | 儲存檔案時 | 自動執行 linter 檢查 |
| `test-before-commit.json` | git commit 前 | 自動執行測試套件，不過不准 commit |

**效果**：每次程式碼變更都經過自動品質閘門，確保持續整合。

### 2.3 Specs（需求先行開發）

| Spec 檔案 | 對應功能 |
|-----------|----------|
| `security-gate-205.md` | Issue #205 安全閘門實作 |
| `celebrity-whale-trades.md` | 名人/鯨魚交易追蹤功能 |
| `issue-244-285-small-screen-layout.md` | Issue #244 #285 小螢幕佈局修正 |
| `backfill-worker.md` | 資料回填 worker 設計 |
| `budget-governance-api.md` | 預算治理 API 設計 |

**效果**：先寫 Spec（需求/驗收條件/設計），再由 Kiro 輔助實作。確保需求明確、可追溯。

---

## 3. 開發歷程摘要

### 3.1 全程 Kiro CLI 開發

- 所有程式碼撰寫、重構、除錯皆在 Kiro CLI (`kiro-cli chat`) 環境中完成
- AI 在 Steering 約束下生成程式碼，自動遵守競賽規則與專案慣例
- 結合 `AGENTS.md` 工作流程：Issue → Spec → Branch → PR → Review → Merge

### 3.2 Steering 管理專案規範與競賽約束

- **競賽硬約束自動執行**：Bedrock-only、反作弊鐵則、15 分鐘時限——寫在 Steering 中，AI 無法繞過
- **信任層公式保護**：TrustScore 權重與來源信譽表固定在 `trust-layer.md`，修改需經審查
- **技術棧限制**：純 stdlib + boto3 原則透過 Steering 自動執行

### 3.3 Hooks 自動化品質管控

- 存檔即 lint：程式碼風格一致
- commit 即測試：確保 regression 不進 main
- 與 CI pipeline 形成雙重保險

### 3.4 Specs 先寫需求再實作

- 每個中大型功能先建立 `.kiro/specs/*.md`
- Spec 包含：需求描述、驗收條件、技術設計、依賴項
- Kiro 根據 Spec 生成實作程式碼，確保對齊需求

### 3.5 完整開發流程

```
Issue（明確驗收條件）
  → Spec（.kiro/specs/*.md）
    → Branch（feat/issue-XXX-...）
      → 實作（Kiro CLI 輔助）
        → Hooks 自動檢查（lint + test）
          → PR（reviewer: cancleeric）
            → /codex-review 對抗性審查
              → Merge → 驗證 post-merge CI
```

---

## 4. 統計數據

| 指標 | 數值 | 備註 |
|------|------|------|
| 已關閉 Issues（全部） | 30 | `gh issue list --state closed` |
| 今日關閉 Issues（7/20） | 28 | 高強度開發衝刺 |
| 已合併 PRs | 30 | `gh pr list --state merged` |
| 測試通過數 | 2,237 | `pytest` (venv 環境) |
| 測試覆蓋率 | 85.73% | 超過 75% CI 閘門要求 |
| Steering 規範檔 | 3 | project / competition / trust-layer |
| Hooks 自動化 | 2 | lint-on-save / test-before-commit |
| Specs 需求文件 | 5 | 中大型功能皆有 Spec |

---

## 5. 結論

TrustForge Hermes 完全符合「採用 AWS Kiro」加分條件：

1. ✅ **Steering**：3 份規範文件管控專案方向與競賽約束
2. ✅ **Hooks**：2 個自動化 hook 確保品質閘門
3. ✅ **Specs**：5 份需求文件實踐「需求先行」
4. ✅ **全流程**：Issue → Spec → Branch → PR → Review → Merge 皆在 Kiro 環境完成
5. ✅ **量化成果**：30 Issues closed、30 PRs merged、2,237 tests passed @ 85.73% coverage

> Kiro 不只是「用 AI 寫 code」，更是用 Steering + Hooks + Specs 建立**結構化的 AI 輔助開發流程**。
