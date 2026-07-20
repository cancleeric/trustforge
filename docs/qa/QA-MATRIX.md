# QA Mini Matrix Report

> 自動產出：`python -m trustforge.cli qa-matrix [--offline]`
> 最新結果：`out/qa-matrix-latest.json`

## 概述

5 幣種 (BTC / ETH / SOL / BNB / XRP) × 3 題型 (multi_source / hypothesis / comparison) = 15 組組合。

每組紀錄：
- **status**：pass / degraded / fail
- **elapsed_sec**：耗時（秒）
- **evidence_count**：Evidence 筆數
- **limits**：含失敗來源清單、降級說明
- **error**：失敗原因（若有）

## 退化判定

報告文字中出現以下 marker 即視為退化：
- `[OFFLINE]`
- `[離線模式]`
- `[SAMPLE]`

## Comparison 配對

為避免 O(n²) 組合，固定每幣一個對照幣：

| 主幣 | 對照幣 |
|------|--------|
| BTC | ETH |
| ETH | BTC |
| SOL | BTC |
| BNB | ETH |
| XRP | SOL |

## 使用方式

```bash
# 離線（測試管線完整性，不需 AWS）
python -m trustforge.cli qa-matrix --offline

# 線上（需網路 + AWS credentials）
python -m trustforge.cli qa-matrix

# 指定輸出目錄
python -m trustforge.cli qa-matrix --out out/qa-run-001
```

## 產出格式

`out/qa-matrix-latest.json`：

```json
{
  "summary": {
    "total": 15,
    "passed": 15,
    "degraded": 0,
    "failed": 0,
    "total_elapsed_sec": 1.0,
    "p95_elapsed_sec": 0.8,
    "offline_mode": true,
    "timestamp": "2026-07-20T08:37:39Z"
  },
  "results": [
    {
      "coin": "BTC",
      "qtype": "multi_source",
      "status": "pass",
      "degraded": false,
      "elapsed_sec": 0.8,
      "evidence_count": 15,
      "limits": ["..."]
    }
  ]
}
```
