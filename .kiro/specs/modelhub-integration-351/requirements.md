# Spec：ModelHub 整合（#351）

> Priority: P1｜前置：#343、#335｜狀態：自動化已實作，live retrain／activation 未執行

## Requirements

### R1：Flat JSONL 是唯一輸入真相 ✅

- `data/training/{BTC,ETH,SOL,BNB,XRP}.jsonl` 進版控。
- loader 驗證 regular file、大小/列數/行長/JSON 型別與 coin 一致性。
- gate 以至少 100 個 unique labelled outcomes 判定；chronological split 明確保留 holdout。

### R2：Defensive ModelHub REST client ✅

- `src/trustforge/modelhub_client.py` 使用 stdlib `urllib.request`。
- 支援 models、retrain-lightning、training-result、external-model path。
- base URL 僅允許 HTTP loopback；`localhost` 正規化為 `127.0.0.1`。
- 停用 proxy/redirect；GET 最多 2 次 bounded retry，POST 不 retry。
- timeout、5 分鐘 poll 上限、response size、finite JSON/schema 驗證與 API key redaction。
- ModelHub 不可達時回結構化狀態，不影響既有分析 pipeline。

### R3：Human-review-only 候選編排 ✅

1. flat loader → gate → chronological split。
2. 送出 train rows、dataset SHA256 與 label-free opaque holdout features。
3. trigger → poll → artifact digest 驗證。
4. 本機以 weighted ECE 比對；`baseline_ece - candidate_ece >= 0.02` 才成為 candidate。
5. durable immutable proposal/execution log 先落地，再發布 per-coin current manifest。
6. `automatic_apply: false` 與 `requires_human_approval: true` 為固定契約。

五幣 live CLI 必須提供五個互異映射：

```text
BTC=<BTC_REQ>, ETH=<ETH_REQ>, SOL=<SOL_REQ>, BNB=<BNB_REQ>, XRP=<XRP_REQ>
```

真實 request number 應由執行者從 ModelHub 核對後填入；不得把單一 registration request
硬套到五幣。macOS 的 `/tmp` 是 symlink，安全路徑檢查會拒絕；測試輸出使用 `/private/tmp/...`。

### R4：Execution budget ✅

編排建立既有 `ExecutionLog`，以其 15 分鐘 deadline 在 trigger/poll/artifact/metric/publication
階段檢查剩餘時間；poll 另 capped at 300 秒。`budget_guard.py` 沒有修改，也不宣稱已修改。

## Failure semantics

| status | 契約 |
|--------|------|
| `blocked` | outcome gate 未過，回 minimum/remaining |
| `unavailable` | ModelHub transport retry 耗盡 |
| `timeout` | poll 或 ExecutionLog budget 不足 |
| `no_improvement` | weighted ECE 改善 < 0.02，不產 proposal |
| `error` | fail-closed；資料、API schema、path 或 durable write 驗證失敗 |
| `candidate` | proposal/log/current 完整，但仍只候選、不得自動啟用 |
| `dry_run` | 不呼叫 ModelHub、不產 current；保留 terminal execution log |

## 驗收證據與邊界

PR #440 merge 後 115 passed、1 skipped。合併後可重現的 relevant suite 為 221 passed；精確
命令與 coverage 限制見 QA 文件。五幣 `/private/tmp` dry-run 成功且 execution-log hashes 重算吻合。
全倉既有 failures 追蹤於 #454；ruff 不可用，因此兩者均不宣稱通過。未執行 live retrain、
activation、DB/secret/Docker/deploy 或 Issue #393。

Live retrain 或 activation 都須先取得 Eric 或具名 ModelHub owner 的明確授權；持有 req_no、
API key 或完成 reviewer/CISO gate 不等於取得該操作授權。
