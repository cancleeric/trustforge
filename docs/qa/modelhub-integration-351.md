# ModelHub 整合 #351：QA 與審查證據

**範圍**：PR #440（defensive REST client）與 PR #447（calibrator proposal orchestration）。
本文件只記錄已親驗的結果；未執行 live retrain、模型 activation、部署或外部服務異動。

## 已驗證

- PR #440 merge 後 client suite：115 passed、1 skipped。
- PR #447 merge 後 relevant suite：221 passed。可重現命令：

  ```bash
  PYTHONPATH=src CACHE_BACKEND=sqlite TRUSTFORGE_DISABLE_ADMIN_CONFIG=1 \
    python3 -m pytest \
    tests/test_modelhub_client.py tests/test_calibrator_gate.py \
    tests/test_modelhub_cli.py tests/test_modelhub_submit.py \
    tests/test_modelhub_training.py tests/test_safe_fs.py -q --no-cov
  ```

- Focused module coverage：`modelhub_submit` 89%、`modelhub_training` 87%、`safe_fs` 86%。
  可重現 invocation：

  ```bash
  PYTHONPATH=src CACHE_BACKEND=sqlite TRUSTFORGE_DISABLE_ADMIN_CONFIG=1 \
    python3 -m pytest \
    tests/test_modelhub_submit.py tests/test_modelhub_training.py tests/test_safe_fs.py -q \
    --cov=trustforge.modelhub_submit --cov=trustforge.modelhub_training \
    --cov=trustforge.safe_fs --cov-report=term
  ```

  此 coverage invocation 仍會載入 repo 全域 `source=src`／`fail-under=75`，所以即使上述三個
  changed modules 都超過 80%，整體 focused invocation 仍以非零結束；不宣稱全域 coverage gate 通過。
- 五幣 `/private/tmp` dry-run 成功；五份 execution log 的 SHA256 均獨立重算吻合，且沒有 current manifest。
- compileall 與 `git diff --check` 通過。

全倉測試存在與本功能無關的既有 baseline failures，追蹤於 #454；因此不宣稱全倉綠。
workspace 沒有 ruff executable，因此不宣稱 ruff 通過。

## Commit-bound gates

PR #447 final reviewed head `2924e4ef65f9949973d2e2f86f5064c3f25b2561`：

- Dev Manager：P0/P1/P2/P3 = 0。
- harper（CISO）：P0/P1/P2/P3 = 0；涵蓋 SSRF、dirfd/TOCTOU、durability/rollback、
  Unicode control character、untrusted result、immutable audit 與 human-approval invariants。
- `/codex-review`：P0/P1/P2/P3 = 0。
- eye breaking-changes scan：0 critical、0 warning。

## Failure semantics

| 狀態 | 意義與產物 |
|------|------------|
| `dry_run` | 完成 loader/gate/package；不發 HTTP、不產 current，只留 execution log |
| `blocked` | labelled outcomes 未達 100；記錄 minimum/remaining |
| `unavailable` | transport retry 耗盡；pipeline 回結構化結果而非洩漏原始例外 |
| `timeout` | poll deadline 或 15 分鐘 ExecutionLog budget 不足 |
| `no_improvement` | weighted ECE 改善未達 0.02；不產 proposal |
| `error` | 資料、回應、路徑或 durable publication 驗證失敗；fail closed |
| `candidate` | proposal/log durable 後才更新 current；仍須人工核准與啟用 |

## 尚未驗證／不在本輪

- 未以真實五幣 `req_no` 執行 live retrain。
- 未下載或啟用任何候選模型，未變更 registry current 狀態。
- Live retrain／activation 只有 Eric 或具名 ModelHub owner 可明確核准；req_no、API key 或
  reviewer/CISO 通過均不等於操作授權。
- 未修改 `budget_guard.py`；15 分鐘限制由既有 `ExecutionLog` 實際執行。
- 未做 DB/migration、secret rotation、Docker、AWS、部署或 Issue #393 回填。
