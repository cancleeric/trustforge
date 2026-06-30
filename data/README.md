````md
# Crypto Market Dataset

本資料包為「加密市場分析 AI Agent」黑客松命題所提供之共同基準市場資料，供參賽隊伍在相同價格資料基礎上進行分析。

## 資料內容

本資料包包含 5 個主流加密資產的 Daily OHLCV 歷史資料：

- BTC
- ETH
- SOL
- BNB
- XRP

資料檔案位於 `data/` 目錄：

```text
data/
  BTC_daily_ohlcv.csv
  ETH_daily_ohlcv.csv
  SOL_daily_ohlcv.csv
  BNB_daily_ohlcv.csv
  XRP_daily_ohlcv.csv
````

## 資料來源

本資料包資料整理自公開市場資料來源。

## 時間範圍

資料期間固定為：

```text
2021-06-01 ~ 2026-05-31
```

時間基準：

```text
UTC 日期
```

## 價格單位

本資料包價格單位為 USDT。

## CSV 欄位

每個 CSV 檔案欄位如下：

```csv
date,open,high,low,close,volume
```

欄位說明：

| 欄位     | 說明                                          |
| ------ | ------------------------------------------- |
| date   | UTC 日期，格式 YYYY-MM-DD                        |
| open   | 當日開盤價，單位 USDT                               |
| high   | 當日最高價，單位 USDT                               |
| low    | 當日最低價，單位 USDT                               |
| close  | 當日收盤價，單位 USDT                               |
| volume | 當日成交量，單位為 base asset，例如 BTC、ETH、SOL、BNB、XRP |

## 資料用途

本資料僅作為參賽隊伍共同使用的基準價格資料。

除本資料包提供之 OHLCV 歷史資料外，其餘新聞、官方公告、鏈上資料、社群情緒、總體經濟、政策與監管事件等資料，均由參賽者自行取得。

## 注意事項

1. 本資料包為黑客松命題用共同基準資料，不構成投資建議。
2. 價格資料可能因資料來源、交易對、流動性或市場狀況而與其他平台略有差異。
3. 本資料包僅提供 OHLCV 歷史資料，不包含新聞、鏈上、社群、總體經濟或其他事件資料。

```
```
