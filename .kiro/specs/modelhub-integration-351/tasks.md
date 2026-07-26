# Tasks

## T1：訓練資料

- [x] 五幣 flat JSONL 進版控且為單一輸入真相
- [x] loader/resource caps/gate/split 測試

## T2：REST client（PR #440）

- [x] client 與五個 API operation
- [x] timeout/retry/graceful fallback
- [x] loopback/redirect/proxy/response/API-key 防線
- [x] mock unit tests 與 opt-in integration test

## T3：編排（PR #447）

- [x] loader/gate/package/client 整合
- [x] label-free opaque holdout 與 weighted ECE
- [x] `--coin`、`--all`、`--req-no-map`、`--dry-run`
- [x] immutable proposal/log、atomic current、failure semantics
- [x] dirfd/TOCTOU 與 fsync/rollback 防線
- [x] 15 分鐘 ExecutionLog budget

## T4：文件與驗收

- [x] README、architecture、spec、QA、handoff 更新
- [x] targeted coverage ≥80%
- [x] Dev Manager、harper、`/codex-review`、eye gates 0 finding
- [x] 五幣 dry-run 與 log SHA256 親驗
- [ ] 取得並核對五幣真實、互異 req_no
- [ ] 經明確授權執行 live retrain
- [ ] 人工審查候選後另行決定 activation
