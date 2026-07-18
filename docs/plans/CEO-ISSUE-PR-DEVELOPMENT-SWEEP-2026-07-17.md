# CEO Issue / PR Development Sweep

日期：2026-07-17
狀態：CEO 審查通過，可執行；merge / deploy 仍需 gate。

## CEO 決策

本輪先處理 issue / PR，不開新主線分散火力。順序固定：

1. PR #218 `Release v0.16.2 Hermes production bugfix`
2. Issue #207 HERMES workspace overlay display performance
3. P0 data foundation issues #215、#209
4. 仍未完成的 H-ID 開發計劃

任何自動排程只能計劃、派工、準備修正、產生 evidence；不得自動 merge、不得自動 deploy。

## PR #218 Gate

現況：

- CI 已綠：Python 3.11、Python 3.12、frontend、Competition QA gate。
- merge state：`CLEAN`。
- reviewer：`nicholaswang941013` 已指定但尚未 approve。

Merge 前仍缺：

- reviewer approval。
- eye scan 紀錄。
- `/codex-review` 對抗審紀錄。
- 因本 PR 包含 Hermes autonomy / cost-control 開關，需 harper/CISO 或等效安全審查。
- CEO 親測確認 Analyze / Compare / History 不再跳掉，且生產成本開關符合預期。

## Issue 處理

### #207 Overlay Display Performance

`develop` 已含 desktop 核心修正：

- `7e4b949 fix: suspend hidden Hermes workspace rendering`
- `b39fb3d fix: preserve hermes workspace navigation`
- `98bd8a9 fix: gate hermes autonomy and stop workspace jump`

仍需在 GitHub issue 補 evidence，並在 #218 merge 後確認是否關閉。若 eye scan 發現 Compare/Analyze 低對比或跳動殘留，另開 follow-up issue，不阻塞已修復的 workspace retention bug。

### #209 Data Contracts

`develop` 已含 `619f2ba feat: version core data contracts`，包含 JSON Schema、CI gate、`schema_version` 與測試。下一步是向 issue 補 commit/test evidence；若 #218 合併後 main 含該 commit，可關閉。

### #215 Source Events

`develop` 已含 `718faf5 feat: archive connector fetches as immutable events`，包含 append-only `source_events`、fetch scheduler 接線與測試。下一步是向 issue 補 commit/test evidence；若 #218 合併後 main 含該 commit，可關閉。

## 30 分鐘 CEO Sweep 規格

每輪自問自答：

- PR 有沒有卡 gate？缺 reviewer、CI、mergeability、eye scan、`/codex-review` 任一項都列為第一優先。
- issue 有沒有已完成但未補 evidence？先同步證據再關閉，不能只改本地 backlog。
- issue 有沒有 P0/P1 未完成？要產出 CPO 計劃、CEO 審查結論、可驗收測試。
- e2e 覆蓋有沒有漏真實工作流？Analyze / Compare / History / workspace URL / cost kill switch 先補。

輸出必須包含：

- ranked execution queue
- owner / next action
- acceptance criteria
- required gate
- blocked reason

禁止：

- 自動 merge
- 自動 deploy
- 繞過 reviewer
- 沒有親測就回報完成

## 下一輪開發順序

1. 等 #218 reviewer approval，同時補 issue evidence。
2. #218 gate 全齊後 merge develop -> main。
3. 建 release branch / tag 前跑完整 backend、frontend、lint、build、local e2e、production smoke 計劃。
4. 若 #207 還有低對比或輕微跳動，另開 UI follow-up，不混進 release hotfix。
5. 回到 H-13a actual coverage、H-24/H-25 diagnostics、H-13b replay evidence。
