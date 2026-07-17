# Hermes 五年歷史來源回填啟動證據

## 結論

五年資料整理已經實際開始，不再只有 adapter 或規劃文件。2026-07-17 已從
Alternative.me 公開歷史介面取得 2021-07-17～2026-07-17 的來源級資料，保存為
provenance-complete JSONL，再匯入本地 SQLite immutable daily snapshots。

## 實際結果

- JSONL：`out/history/alternative-me-fng-2021-07-17_2026-07-17.jsonl`
- SHA-256：`45be4fe47605422506c9e49b72b0e3d0e03d0f0690561d5f4a991d4c59036965`
- 來源 rows：9,130（1,826 天 × BTC/ETH/SOL/BNB/XRP）
- 日期區間的預期日數：1,827
- 每幣實際 snapshot：1,826，coverage `0.999453`
- 明確缺日：`2024-10-26`（五個幣別相同，來源回應未提供該日）
- SQLite：`out/trustforge.sqlite3`
- coverage artifact：`out/history/historical-coverage-2021-07-17_2026-07-17.json`

因此目前能誠實聲稱的是「Alternative.me 五年區間近完整回填」，不是「五年所有
來源都已完整」。SEC filing 全文、CoinGecko range、on-chain history、新聞與
Reddit archive、HOYA 仍是 missing/gated/blocked，不得以 ready 標籤或 OHLCV
冒充來源級 Evidence。

## 完整 replay 與 outcome 結果

2021-07-17～2026-07-17 已對五幣執行完整離線 replay：

- BTC/ETH/SOL/BNB/XRP 各成功 1,826 日，共 9,130 個 replay。
- 每幣唯一 skipped 都是 `2024-10-26: snapshot_missing`。
- `scripts/audit_historical_replay_batch.py` 驗證 9,130 個 artifact 全部為
  `backfilled_archive`，且 Execution Log 同時包含 `historical_replay.start` 與
  `historical_replay.done`；`invalid=0`。
- audit artifact：`out/replay/five-year-audit.json`。
- T+1/T+7/T+14 outcome labeling 各產生 1,826 rows × 5 幣；目前 eligible 全為
  0，因 Alternative.me 單一市場情緒來源不足以產生 `偏多/偏空` 的正式方向判斷，
  Hermes 誠實輸出「不明」。這不能用假方向補標，也不解除 calibrator gate。

首次完整 replay 另揭露同日 live snapshot 與 backfill 共用 key 的污染風險；已將
`__source_snapshot_history__` 與 `__source_snapshot_backfill__` 分離。歷史 runner
明確只讀 `archive_type=backfilled_archive`，2026-07-17 隔離回歸已通過。

## 重現命令

```bash
.venv/bin/python scripts/fetch_public_history.py \
  --source alternative-me-fng \
  --from-date 2021-07-17 --to-date 2026-07-17 \
  --out out/history/alternative-me-fng-2021-07-17_2026-07-17.jsonl

CACHE_BACKEND=sqlite \
TRUSTFORGE_SQLITE_PATH="$PWD/out/trustforge.sqlite3" \
.venv/bin/python scripts/report_historical_coverage.py \
  --from-date 2021-07-17 --to-date 2026-07-17 \
  --out out/history/historical-coverage-2021-07-17_2026-07-17.json

.venv/bin/python scripts/audit_historical_replay_batch.py \
  --replay-root out/replay --out out/replay/five-year-audit.json
```

`historical_coverage_report` 只計算 SQLite 中真正存在的 snapshot/source/document，
能力矩陣的 `ready`、`gated` 或 `blocked` 狀態不會增加 coverage。
