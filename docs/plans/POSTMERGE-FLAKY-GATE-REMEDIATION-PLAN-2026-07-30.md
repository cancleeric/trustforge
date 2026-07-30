# Develop post-merge flaky gate 修復計劃（2026-07-30）

## 決策與範圍

狀態：**CEO 已核准，可依本計劃實作。**

CEO 審查結論（2026-07-30）：**APPROVED**。根因、範圍、相依性與
fail-closed 驗收條件完整；修復限於測試隔離與決定性同步。若需要更動
production code、放寬 timeout/assertion，或超過 6 小時，必須停止並重新規劃。

develop post-merge gate 連續三輪出現四次互異失敗，集中在三個測試邊界：

1. `tests/test_evidence_transaction_store.py::test_short_write_is_retried`
2. `tests/test_preview_lease_recovery.py::test_midpage_deadline_checkpoints_exact_item_and_next_run_resumes`
3. `frontend/src/pages/HermesDashboard.test.tsx` 的 N2 error-state／submit reset 情境

本計劃只修測試隔離與決定性，不改 production 行為、契約、逾時值或錯誤處理語義。禁止加入 retry、重跑失敗案例、放寬 assertion、延長全域 timeout，或以 sleep 掩蓋 race。

總工時上限：**6 小時**。若任何項目需要修改 production code，立即停止並另開 issue／計劃，不得以本計劃擴張範圍。

## 根因假說與修復設計

### F1 — Evidence transaction short-write 測試隔離（1.25h）

現況測試 monkeypatch 全域 `os.write`，第一次遇到任意大於一 byte 的 write 都會短寫。平行測試、pytest capture、coverage 或其他執行緒若先使用 `os.write`，可能消耗一次性注入，甚至誤傷非目標 FD。

修復：

- 先取得 transaction store 寫入目標的明確 FD／可驗證身份。
- short-write hook 僅在 `fd == target_fd` 時注入一次短寫；其他 FD 原樣委派 `real_write`。
- 以事件或明確計數確認注入確實命中目標 FD，並保留「短寫後完整 payload 成功落盤」的真實行為驗證。
- 同類 zero-progress 測試也必須限制到目標 FD，避免 tombstone／pytest capture 被全域 hook 誤傷；仍驗證 zero progress 導致 fail-closed，不改 production 重試邏輯。

驗收：

- short-write 與 zero-progress 測試單獨、同檔及平行模式皆通過。
- assertion 證明注入只命中目標 FD，且非目標 FD 未被改寫。

### F2 — Preview lease recovery deterministic deadline（1.25h）

現況 deadline 測試以有限 iterator 模擬 monotonic clock；clock 呼叫次數與執行路徑／機器效能耦合，容易因額外 checkpoint 或排程差異提前耗盡或在錯誤 item 截止。

修復：

- 注入可由測試明確推進的 deterministic clock，不使用 wall clock、sleep 或執行耗時。
- 以明確 deadline event／checkpoint 計數控制：前兩個 item 完成後才跨過 deadline。
- 驗證第一輪 checkpoint 精確落在第二筆 item，第二輪從相同 cursor 恢復並只處理第三筆。
- 保留 production 的 mid-page deadline、CAS watermark 與 resume 行為；不得增加 production retry 或放寬 candidate/cursor assertion。

驗收：

- clock 不會因額外讀取而耗盡，deadline 發生位置由測試事件決定。
- 原有 `candidates == 2`、`last_sk == item[1]`、下一輪 `candidates == 1` 全部保留。

### F3 — HermesDashboard error-state 明確同步（1.75h）

現況測試依賴模組層共享 mock 與 `waitFor` 的固定短輪詢窗口。前序案例留下的 mock 狀態、React effect 排程或 promise 完成時機，都可能讓 error telemetry 尚未送達就檢查 submit 狀態。

修復：

- 每個案例在 `beforeEach` 建立全新 deferred promise／event，清除 mock 呼叫與一次性 implementation，禁止跨案例共享 pending promise。
- 由測試明確 resolve/reject `registerAnalysisQuestion`（或對應 error completion promise），再等待可觀測的 error-state event／DOM 狀態。
- 只有收到 error completion 後才斷言 submit 恢復 enabled、loading label 消失。
- 不使用固定 sleep、不增加 retry、不延長產品 timeout；`waitFor` 僅用於 React DOM flush，成功條件由明確 promise/event 驅動。
- 同檔其他會觸發 submit/error 的案例須確認各自擁有獨立 mock lifecycle。

驗收：

- 測試真實覆蓋「embedded AnalyzePage 進入 error state 後，HermesDashboard submit phase 回到 ready」。
- 失敗 promise 未完成前按鈕保持 loading；事件完成後才恢復，避免只測到初始狀態或假陽性。

### F4 — 重現與 release-gate 證據（1.25h）

依序執行：

1. 三個目標案例各自重複至少 20 次。
2. 三者組合重複至少 20 次；後端案例另以既有平行 pytest 模式驗證。
3. frontend `HermesDashboard.test.tsx` 全檔重複至少 20 次，確認無共享狀態污染。
4. 在同一 frozen commit 上連續執行兩次 `.githooks/pre-push`，兩次均須完整全綠。
5. `git diff --check`，並記錄 commit SHA、測試命令、輪次與結果。

若 20 次壓測仍出現一次失敗，本計劃不得標記完成；須保留失敗證據並回到對應根因項目。

## 執行順序與相依性

1. F1、F2 可各自在獨立 scoped branch/worktree 實作，彼此無程式相依。
2. F3 可平行實作，但必須使用獨立 frontend mock lifecycle。
3. 三項修復整合到單一候選 commit 後才執行 F4；不得以各自分支的局部綠燈取代整合驗證。
4. `/codex-review` 必須確認沒有 production behavior change、retry、sleep、timeout 放寬或 assertion 弱化。
5. reviewer finding 全數修正後重跑受影響的 20 次壓測，再重新執行兩輪完整 pre-push。

## 完成定義

- 三個目標測試及相關同檔測試各自／組合 **repeated ≥20 次全綠**。
- 同一 frozen commit 上 `.githooks/pre-push` **連續兩次全綠**。
- `git diff` 僅包含測試隔離、fixture、deterministic clock／promise-event 同步；**no production behavior change**。
- 沒有 retry、sleep、全域 timeout 延長、失敗吞噬或 assertion 放寬。
- 總實作與驗證工時 **≤6h**；超時或需改 production code 時停止並重新規劃。
- PR 記錄 reviewer、`/codex-review` finding/fix、精確 commit-bound gate 證據；此範圍沒有 UI 視覺變更，Eye 標記為不適用並說明原因。

## 非目標

- 不調整 evidence transaction store、lease recovery 或 HermesDashboard 的 production 邏輯。
- 不修改 pre-push gate 的測試集合、worker 數、timeout 或失敗政策。
- 不以 quarantine、skip、xfail、重跑插件或 CI retry 降低 flaky 可見性。
- 不處理這三個測試以外的新失敗；新失敗須保留證據並另行分類。
