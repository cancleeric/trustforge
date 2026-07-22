# TrustForge × ModelHub 整合交接（#351）

**日期**：2026-07-22｜**狀態**：自動化已合併；live retrain／activation 尚未執行

## 1. 一句話現況

PR #440 已交付 defensive ModelHub client，PR #447 已交付五幣 calibrator proposal
編排；目前可安全 dry-run，所有候選都是 human-review-only。下一位接手者要做的是取得並核對
五個真實、互異 `req_no`，經授權後做 live retrain 驗證，而不是再重寫 R2/R3。

## 2. 已完成與可查證範圍

- 輸入：`data/training/{BTC,ETH,SOL,BNB,XRP}.jsonl` flat JSONL。
- client：loopback-only、no proxy/redirect、GET bounded retry、POST no retry、API key redaction。
- orchestration：gate、split、label-free holdout、trigger/poll/path、weighted ECE、resource caps。
- publication：immutable proposal/execution log，atomic per-coin current，dirfd/TOCTOU 與 fsync rollback。
- policy：`automatic_apply: false`、`requires_human_approval: true`。

既有人工 ModelHub 登記紀錄是 `MH-2026-075`（registry
`trustforge-calibration-isotonic` v1.0.0）；它不是五幣 live mapping 的替代品。空 draft
`MH-2026-074` 保留，不在本 repo 刪除，也不阻塞自動化。

## 3. 五幣 req_no 與直接操作方式

先做無網路副作用的 dry-run：

```bash
PYTHONPATH=src python3 -m trustforge.cli modelhub-train --all --dry-run \
  --training-dir data/training \
  --out-dir /private/tmp/trustforge-modelhub-proposals
```

macOS `/tmp` 是 symlink，安全路徑檢查會刻意拒絕；請用 canonical `/private/tmp/...`。
live 模式必須提供五個不同 request number：

```bash
PYTHONPATH=src python3 -m trustforge.cli modelhub-train --all \
  --req-no-map "BTC=$MODELHUB_BTC_REQ" --req-no-map "ETH=$MODELHUB_ETH_REQ" \
  --req-no-map "SOL=$MODELHUB_SOL_REQ" --req-no-map "BNB=$MODELHUB_BNB_REQ" \
  --req-no-map "XRP=$MODELHUB_XRP_REQ" \
  --out-dir /private/tmp/trustforge-modelhub-live
```

執行前逐一向 ModelHub 查證 mapping，且 API key 只放環境變數／vault，禁止寫入文件、shell
歷史或 commit。state-changing retrain 必須先取得 Eric 或具名 ModelHub owner 的明確授權；
取得 req_no、API key 或通過 reviewer/CISO 審查都不構成操作授權。本交接沒有虛構五個
request number，也沒有授權 state-changing retrain。

## 4. 輸出與 failure semantics

| 產物／狀態 | 行為 |
|-------------|------|
| `execution-<run_id>.jsonl` | immutable terminal audit log，回傳 SHA256 |
| `<coin>-<dataset_sha>-<run_id>.json` | ECE 改善至少 0.02 才建立的 immutable proposal |
| `<COIN>.json` | per-coin current；proposal/log durable 後才原子更新 |
| `blocked` | unique labelled outcomes <100，附 minimum/remaining |
| `unavailable` | transport retry 耗盡，不洩漏 raw exception/API key |
| `timeout` | 5 分鐘 poll 或既有 ExecutionLog 15 分鐘 budget 不足 |
| `no_improvement` | weighted ECE 改善不足，不產 proposal |
| `error` | data/API/path/durable write 驗證失敗，fail closed |
| `dry_run` | 不呼叫 ModelHub、不產 current，只留下 execution log |

`budget_guard.py` 未修改；15 分鐘限制由既有 `ExecutionLog` 實際執行。

## 5. 安全與人工審查契約

- ModelHub base URL 僅允許 HTTP loopback，禁止 proxy 與 redirect。
- validation label/hit 不會送出；holdout 以 CSPRNG opaque id 對齊。
- Model path 視為 untrusted string，不由編排直接開啟。
- input/output 透過 pinned directory fd，拒絕 symlink、FIFO 與 parent swap race。
- candidate 不等於 activation；程式沒有 registry promotion／current model 套用功能。
- 不做 DB/migration、secret rotation、Docker、AWS、部署或 Issue #393。

## 6. 已完成 QA／審查證據

- PR #440 merge 後 client：115 passed、1 skipped。
- PR #447 merge 後可重現 relevant suite：221 passed（精確命令見 QA 文件）。
- focused coverage：`modelhub_submit` 89%、`modelhub_training` 87%、`safe_fs` 86%；coverage
  invocation 因 repo 全域 source/fail-under 設定以非零結束，未宣稱全域 coverage gate 通過。
- 五幣 `/private/tmp` dry-run 成功；五份 execution-log SHA256 親自重算吻合，0 current manifest。
- final head `2924e4e...`：Dev Manager、harper CISO、`/codex-review` 均 0 finding；eye
  breaking-changes scan 0 critical／0 warning。
- compileall 與 diff-check 通過。

全倉既有 failures 已另追 #454，故不宣稱全倉綠；workspace 無 ruff executable，故不宣稱
ruff 通過。也不宣稱 live retrain、activation 或部署完成。完整紀錄見
`docs/qa/modelhub-integration-351.md`。

## 7. 接手檢查清單

- [ ] 確認工作樹，勿納入持續變動的 `data/training/BTC.jsonl`、`ETH.jsonl` 或其他人文件。
- [ ] 從 ModelHub 核對 BTC/ETH/SOL/BNB/XRP 五個真實且互異的 `req_no`。
- [ ] 確認 API key 只由 vault/環境變數提供，沒有出現在 log、命令輸出或 commit。
- [ ] 先重跑五幣 `/private/tmp` dry-run，重算每份 execution log SHA256。
- [ ] 取得明確授權後才執行 state-changing live retrain。
- [ ] 逐幣檢查 status、dataset SHA、proposal/log/current 引用與 weighted ECE。
- [ ] 對任何 candidate 做人工 reviewer + harper + `/codex-review`；只有 Eric 或具名 ModelHub
  owner 可另行核准 activation，審查通過本身不構成 activation 授權。
- [ ] 保留 `MH-2026-074`；若要刪除，交由 hCore admin 依外部服務流程處理。
