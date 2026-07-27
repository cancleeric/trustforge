# Shadow observation operator handoff

Issue #732 的 shadow runtime 只產生非權威證據。本流程沒有啟用、promotion、
流量切換或 production cutover 指令；`eligible_for_operator_review` 也只代表可將
證據交給具名 reviewer，絕不代表候選核心已獲准上線。

## 安全邊界與準備

1. 使用專用、不可登入的 OS 帳號執行 observation runtime。資料庫目錄必須由該
   UID 擁有、mode `0700`；DB 與 attestation 必須 mode `0600`。不得和 web
   runtime 共用 UID，也不得使用 symlink。
2. 先驗證 active release manifest 及其 artifact SHA-256。attestation JSON
   只能包含：
   `version`、`dedicated_runtime`、`active_manifest_path`、
   `active_artifact_path`；`version` 必須為
   `trustforge.shadow-runtime-attestation/v1`，且
   `dedicated_runtime` 必須為 `true`。
3. 精確設定下列環境變數，不可使用 floating tag：

   - `TRUSTFORGE_SHADOW_DB_PATH`：專用 UID 所有之絕對 DB 路徑。
   - `TRUSTFORGE_SHADOW_DEDICATED_RUNTIME=1`
   - `TRUSTFORGE_SHADOW_RUNTIME_ATTESTATION_PATH`：owner-only 絕對路徑。
   - `TRUSTFORGE_SHADOW_ACTIVE_RELEASE`
   - `TRUSTFORGE_SHADOW_CANDIDATE_RELEASE`
   - `TRUSTFORGE_SHADOW_ACTIVE_ARTIFACT_DIGEST`
   - `TRUSTFORGE_SHADOW_CANDIDATE_ARTIFACT_DIGEST`

   release 值及 digest 必須與量測的 manifest、active artifact 和 repository
   內 reviewed candidate manifest 完全一致；不一致時 runtime 和 health
   report 都 fail closed。

## 開始及停止觀察

預設必須維持：

```text
TRUSTFORGE_SHADOW_RUNTIME_ENABLED=0
KERNEL_SHADOW_OBSERVE=0
```

在具名 operator 記錄 issue、release tuple、attestation digest、開始時間及
rollback owner 後，才可在專用 observation runtime 同時設為 `1` 並重啟該
runtime。這兩個 flag 不可設定在 active web runtime，也不會改變 active result。

停止觀察時，先將兩個 flag 都設回 `0`，重啟專用 observation runtime，確認新
request 不再增加 observation event，再保全 DB、WAL、SHM、manifest 與
attestation 的 digest。任何異常都先停止，不得為了補樣本放寬 policy。

## 24 小時完成條件

以同一組 exact active/candidate artifact、policy digest 和 contract version
連續收集 24 小時。唯讀檢查：

```text
trustforge shadow-health --pretty
```

exit code `0` 僅代表 `eligible_for_operator_review`；`2` 代表繼續觀察；`3`
代表停止或 evidence/identity/attestation 無法驗證。報告必須保存完整 JSON，
並確認：

- 至少 30 個完成 observation、至少 3 個 coins、至少 2 個 question types。
- 每一個實際 coin × question-type Cartesian cell 至少 2 筆。
- parity pass rate 至少 `0.90`。
- confidence delta 與 trust delta 每筆皆不超過 `0.05`。
- supporting-claim Jaccard 每筆皆不低於 `0.70`。
- operational latency p95 不超過 `250 ms`，每筆不超過 `1000 ms`。
- provider calls 與 cost 每筆及總計皆為 `0`。
- 第三個連續 terminal failure 必須產生 stop；不得重新排序或刪除證據規避。
- schema、policy、release manifest、runtime attestation 和 completion evidence
  全部為 true。
- 報告含 exact identity、scenario counts、blockers、observation root、
  deterministic aggregate/decision IDs 和有序 observation IDs。

缺 DB、損毀、過期或 future evidence、orphan completion、identity 混用、重播、
不完整矩陣或任何上限失敗，都不得交接為 eligible。

## 人工 reviewer 交接

operator 將 report JSON、DB/WAL/SHM digest、release manifest、attestation、
pre-push 證據、Eye 結果及 `/codex-review` 紀錄附在 PR/issue，指定 reviewer。
reviewer 必須重新在同一 immutable release tuple 執行 health report 並比對
evidence root 與 decision ID。安全與成本項目另需 harper 審查。

即使 report 為 eligible，本里程碑仍只完成「人工審查交接」。後續若要 production
activation，必須另開 issue、獨立 release-level A/B 計劃與 rollback approval；
不得從本 runbook 推導或執行切換。

## Disable / rollback

1. 將 `KERNEL_SHADOW_OBSERVE=0` 與
   `TRUSTFORGE_SHADOW_RUNTIME_ENABLED=0`，重啟專用 observation runtime。
2. 確認 active web runtime 未設定兩個 flag，active release/artifact 未改變。
3. 保全 evidence 檔案，不得 UPDATE/DELETE；記錄停止原因與最後 event ID。
4. 若專用 runtime 仍產生 event，停止該服務並隔離其 UID；active traffic 不需也
   不得切換，因 shadow output 從未成為 active result。
