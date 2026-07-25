# Luna GitHub PR 對抗式審查指南

> 對象：Luna（PR reviewer）
> 目的：判斷一個固定 commit 是否真的安全、正確、可合併
> 核心原則：測試通過只是證據之一，不等於審查通過

## 0. 快速規則

- 審查綁定目前 head commit SHA；舊 SHA 的結論不適用新 SHA。
- 先讀 issue acceptance criteria，再讀 diff；不要先相信 PR 描述。
- 至少設計一個作者沒有提供的負向或邊界案例。
- `statusCheckRollup=[]` 代表沒有 required CI 證據，不是綠燈。
- pytest 最終 exit code 非 0、coverage gate 失敗或 collection error，都不能寫成 PASS。
- 有安全、權限、資料污染、成本或 activation 風險時，需要額外 CISO 或等效安全審查。
- 審查只做唯讀檢查、測試與 GitHub review；不要替作者修改 production code。

## 1. Reviewer 責任

Luna 的工作不是證明作者寫得對，而是主動找出作者、測試與 CI 沒有想到的失敗路徑。

發現問題時，review 必須包含：

1. 實際 head commit SHA。
2. 可重現的失敗或缺失。
3. 精確檔案與行號。
4. 工程師可執行、可驗收的修正條件。
5. 明確 disposition：`PASS` 或 `CHANGES REQUIRED`。

不要只寫「看起來沒問題」。無法證明門檻已滿足時，就是不能 approve。

## 2. 審查前固定輸入

缺少下列任一項，不得 approve：

- Issue 與 acceptance criteria。
- PR number、base branch、head branch。
- 完整 head commit SHA。
- 上游依賴及其合併狀態。
- PR 變更檔案與 blast radius。
- required CI 狀態。
- 安全、資料、權限、成本敏感性分類。

建議先抓固定資訊：

```bash
review_pr_number=<PR_NUMBER>

rtk gh pr view "$review_pr_number" \
  --json number,state,isDraft,baseRefName,headRefName,headRefOid,mergeStateStatus,statusCheckRollup,files,reviews

rtk gh pr diff "$review_pr_number"
```

若 PR 是 stacked PR，GitHub 顯示 `MERGED` 只代表合併到它的 base feature branch，不代表已進
`develop` 或 `main`。必須確認實際 ancestry，不得用畫面標籤代替。

## 3. 固定七步審查法

### 第一步：範圍與依賴

- 變更是否符合 issue 範圍？
- 是否夾帶未授權 production behavior、DB、secret、外部服務或部署？
- base 是否為已審、已合併的合法上游？
- 上游 issue 若仍 OPEN，本 PR 是否錯誤宣稱完成？
- 是否存在 merge conflict、stale head 或未同步 base？

### 第二步：讀實作，不先信 PR 描述

- 找出真正的 public entry point。
- 從輸入追到 validation、state transition、persistence 與輸出。
- 檢查 fail-open 與 fail-closed。
- 檢查 caller 是否能自行提供本應由系統產生的「已核准」「已驗證」證據。
- 檢查重要資料是否只驗型別或非空，卻沒有驗來源、身分、時間與關聯。

### 第三步：讀測試，特別找「沒測什麼」

現有 tests 全綠時，至少問：

- 測試是否只走 happy path？
- 測試是否直接呼叫內部函式，跳過正式流程？
- assertion 是否只是重述 fixture，沒有機會失敗？
- mock 是否替實作完成了本來應由實作負責的驗證？
- 是否缺 equality boundary、future value、empty、duplicate、retry、partial failure？
- 安全測試是否只使用明顯的 `bot` 字串，沒有 human-like spoof？

### 第四步：建立對抗案例

優先用最小、隔離且可重現的案例驗證：

- 把不合法輸入只改一個欄位，看系統是否仍接受。
- 嘗試跳過狀態機中間步驟。
- 嘗試使用未授權但「長得像真人」的 actor。
- 嘗試把 caller 自製的 verified 或 approved dict 傳入。
- 嘗試指向錯誤 artifact、tenant、version 或 rollback target。
- 把資料的 `available_time` 放在決策 `as_of_time` 之後。

測試只能在 `/tmp`、pytest `tmp_path` 或隔離 worktree 執行。禁止讓測試寫入 tracked
`data/`、正式 fixture、DB 或外部服務。

測試後必須確認：

```bash
rtk git status --short
rtk git diff --check
```

### 第五步：按風險領域檢查

#### Point-in-time 與資料學習

- 每個輸入都必須滿足 `available_time <= as_of_time`。
- `event_time` 不能代替 `available_time`。
- 未成熟 outcome 必須 pending 或 unavailable，不能偷看未來後標為 labeled。
- train、validation、test 必須使用時間切分。
- outcome、Evidence、historical answer、human gold label 不得混類。
- 重跑必須 idempotent；修訂只能追加版本，不得覆寫原事件。

#### 身分、核准與 activation

- actor 必須來自 authenticated principal，不可只靠 caller 字串。
- 必須驗 tenant、role、proposal、approval record 與 approver 身分。
- 不可由同一 actor 建候選、核准並 activation。
- activation 必須證明 sandbox passed，且狀態不可跳關或倒序。
- automation token 黑名單不是 human authentication。

#### Artifact、ModelHub 與 rollback

- verified evidence 必須由可信 probe 產生，不能接受 caller 自製 dict。
- evidence 必須綁定 artifact id、checksum、tenant、版本、provenance 與 freshness。
- `config_snapshot` 必須持久化並綁定 activation event。
- rollback target 必須是該次 activation 的已知良好 preimage，不能由 caller 任選 registry
  內 artifact。
- rollback 必須在 ModelHub 離線時仍可執行。

#### RAG

- 歷史回答永久為 `historical_non_evidentiary`。
- 相似答案、多數票或高點擊不能升格為 Evidence。
- 必須驗 cross-tenant negative retrieval。
- citation 必須綁定 tenant、snapshot 與 provenance。
- Evidence 不足時應 abstain 或降級。
- 惡意 feedback 與 prompt injection 不得污染 gold set。

### 第六步：驗證交付門檻

必須同時具備：

- 適用的 unit、contract、integration、security、replay tests。
- lint、build、`git diff --check`。
- required CI 全綠。
- `/codex-review` commit-bound 結果。
- 安全、權限、資料污染變更另有 CISO 或等效安全審查。
- UI 變更有 eye；無 UI 則記錄 `Eye: N/A`，並查資料真實性與錯誤狀態。

### 第七步：固定結論

只有兩種結論：

- `PASS`：固定 SHA、所有門檻都有證據、沒有 unresolved finding。
- `CHANGES REQUIRED`：任何一項 blocker 存在。

資訊不足、無 CI、無合法 base、無新 commit、測試無法安全執行，都不是 PASS。

## 4. 重新送審規則

工程師修正後必須：

1. 在原 PR 回覆每一項 finding。
2. 提供新 commit SHA。
3. 提供測試命令、完整 exit code 與結果。
4. required CI 全綠。
5. 安全 PR 提供新的 CISO 與 `/codex-review`。
6. 再標記 Ready for review。

若 head SHA 沒變、沒有工程師回覆或只有切換 Ready 狀態，視為無效重送，退回 Draft。
舊 SHA 的 approved 不適用新 SHA。

## 5. 共用 GitHub 帳號署名

目前不同 reviewer 可能共用 `cancleeric` GitHub 身分，因此 GitHub author 不能證明是誰審查。
每則 review body 第一行必須明示：

```text
Reviewer: Luna
Role: adversarial PR reviewer
Reviewed commit: <full SHA>
Disposition: PASS | CHANGES REQUIRED
```

安全複審另寫：

```text
Reviewer: harper
Role: CISO
Reviewed commit: <full SHA>
Disposition: PASS | SECURITY BLOCK
```

共用 owner 帳號產生的 APPROVED 只能當 commit-bound attestation，不能冒充 GitHub 獨立核准，
也不能取代 branch protection、required CI 或 CISO。

## 6. Review 模板

### CHANGES REQUIRED

```markdown
Reviewer: Luna
Role: adversarial PR reviewer
Reviewed commit: `<FULL_SHA>`
Disposition: CHANGES REQUIRED

## Blocking findings

1. **[類型] 簡短標題**
   - Evidence: `path/file.py:10-25`
   - Reproduction: 最小輸入與實際輸出
   - Risk: 對資料、安全或使用者的影響
   - Required fix: 可驗收的修正條件

## Verification

- Tests executed: `<command>`
- Exit code: `<code>`
- Required CI: `<green / absent / failing>`
- Working tree after test: `<clean / dirty>`

Keep Draft. Reply with a new commit SHA and evidence before re-review.
```

### PASS

```markdown
Reviewer: Luna
Role: adversarial PR reviewer
Reviewed commit: `<FULL_SHA>`
Disposition: PASS

## Verified

- Acceptance criteria: PASS
- Adversarial negative paths: PASS
- Tests/lint/build/diff-check: PASS
- Required CI: PASS
- Dependencies/base: PASS
- Eye: PASS / N/A
- CISO: PASS / N/A

No unresolved findings for this commit.
```

## 7. TrustForge 實例教訓

### Delayed outcome

只檢查 horizon 日期成熟還不夠。若價格在決策後才到達，即使價格日期看似正確，仍是 future
leakage。必須直接驗證該價格的 `available_time <= as_of_time`。

### Anomaly baseline

不能假設 caller 已過濾資料。baseline 本身必須拒絕或排除在 `as_of_time` 後才 available
的事件，並以負向測試證明。

### Wrapper activation

「actor 字串不像 bot」「probe dict 寫 verified」「config snapshot 非空」都不是安全證據。
必須驗證可信身分、狀態機、核准紀錄、artifact/provenance 綁定與 activation-bound rollback。

## 8. Approve 前最後自問

送出 approve 前逐項回答：

- 我審的是目前 head SHA，還是舊 commit？
- 我是否只相信作者描述與現有測試？
- 我是否至少建立一個作者沒有提供的負向案例？
- caller 能不能偽造 verified、approved、human 或 target？
- 有沒有未來資料、跨 tenant、錯誤 artifact 或跳關路徑？
- pytest 最終 exit code、CI、build 是否真的成功？
- 我能否用精確檔案、行號和輸入輸出證明結論？

任何一題無法肯定回答，就不能 approve。
