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

## Replay smoke

BTC 2021-07-17～2021-07-23 已以 immutable snapshots 執行七個 daily replay，
輸出在 `out/replay/alternative-me-smoke-btc/`，共七個 daily JSON 與一個 index，
`skipped=[]`。這證明匯入資料可進 replay，但不代表五年全幣完整 batch 已完成。

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
```

`historical_coverage_report` 只計算 SQLite 中真正存在的 snapshot/source/document，
能力矩陣的 `ready`、`gated` 或 `blocked` 狀態不會增加 coverage。
