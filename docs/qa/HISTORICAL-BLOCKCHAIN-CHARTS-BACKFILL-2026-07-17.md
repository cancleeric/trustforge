# Blockchain.com 五年鏈上歷史回填證據

## 結果

- 區間：2021-07-17 至 2026-07-17（UTC，預期 1,827 個日界線）。
- 官方來源：Blockchain.com Charts API，固定白名單指標 `n-transactions`、
  `hash-rate`、`difficulty`；僅適用 BTC。
- 原始匯出：5,469 rows，三個指標各 1,823 日；JSONL SHA-256：
  `2cd3ef0d71e3469ce1b4008eaaec32034600b08ff374bf3aaf5bd71573c85a39`。
- 供應商缺日：2025-11-13、2025-11-14、2025-11-15、2026-07-17。
  最後一天尚未形成完整日結，未以空值或今天的資料補造。
- SQLite 寫入：1,823 個 `backfilled_archive` daily snapshots；與既有
  Alternative.me 日期聯集後，BTC snapshot coverage 為 1,827/1,827。
- 重跑：BTC 1,827 runs、0 skipped；全五幣 audit 共 9,131 runs，
  `complete=true`、`invalid=0`。
- Outcome：BTC 1,827 labels；T+1/T+7/T+14 eligible 均為 0。鏈上原始數值
  沒有被錯誤解讀為價格方向。

## 安全與語意邊界

- URL、hostname 與 chart 名稱均由程式固定，經現有 HTTPS、憑證驗證、DNS
  pinning 與 SSRF 防護抓取。
- 實抓發現 macOS framework Python 的系統 CA 不完整；正式 fetch helper 已改用
  certifi Mozilla CA bundle，仍維持 `CERT_REQUIRED`、hostname verification 與
  TLS 1.2 下限。修正後以 2026-07-15 單日三指標 smoke 實測成功，不需關閉驗證。
- Provider 回應的名稱與單位不進 Evidence；顯示文字使用本地固定 label。
- 非有限值、負值、未知 chart 與錯誤 envelope 一律拒絕。
- Capability 宣告 `coins=[BTC]`；外框缺口診斷不會把這個 BTC-only 來源誤報
  成 ETH、SOL、BNB、XRP 缺口。

官方契約與介面：

- [Blockchain.com Charts API](https://www.blockchain.com/explorer/api/charts_api)
- [Blockchain.com Explorer API](https://www.blockchain.com/explorer/api)

## 可重現命令

```text
python3 scripts/fetch_public_history.py --source blockchain-com-charts --from-date 2021-07-17 --to-date 2026-07-17 --out out/history/blockchain-com-charts-2021-07-17_2026-07-17.jsonl
CACHE_BACKEND=sqlite TRUSTFORGE_SQLITE_PATH=out/trustforge.sqlite3 python3 scripts/historical_backfill.py out/history/blockchain-com-charts-2021-07-17_2026-07-17.jsonl --coin BTC --from-date 2021-07-17 --to-date 2026-07-17
CACHE_BACKEND=sqlite TRUSTFORGE_SQLITE_PATH=out/trustforge.sqlite3 python3 scripts/run_historical_replay_batch.py --coin BTC --from-date 2021-07-17 --to-date 2026-07-17 --out-dir out/replay/five-year-btc
```
