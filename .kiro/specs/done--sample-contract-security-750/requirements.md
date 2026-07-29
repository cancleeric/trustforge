# 需求：Sample Contract 安全性、PIT 強化與同日多來源

> Issue: #750
> Parent: #749
> Labels: security, data-integrity, research-remediation
> PR: #754 (merged to develop)

## 背景

`scripts/build_historical_samples.py` 存在三類嚴重方法論與安全缺陷：

1. **安全漏洞**：程式中使用 `eval()` 解析輸入，允許任意程式碼注入。
2. **PIT 違規**：宣稱實作 Point-in-Time（PIT）gate，但未真正強制 evidence 的 visible/published timestamp 必須 <= `as_of`；未來時間戳的資料可混入 features。
3. **同日多來源覆寫**：相同日期有 FNG + replay + onchain 等多 family 資料時，後者會覆寫前者，造成資料遺失。

## 範圍

修正 `docs/contracts/historical-sample-contract.md` 規格與 `scripts/build_historical_samples.py` 實作，並新增對應測試。

## 功能需求

### FR-1: 移除 eval/exec，純 JSON 輸入

- 完全移除任何 `eval()` / `exec()` 呼叫。
- 輸入只接受結構化 JSON/JSONL 或 CSV；malformed 行排除並計數。
- 拒絕 symlinks、非常規檔案、超過安全上限的檔案。

### FR-2: PIT Gate 嚴格化

- 每條 evidence 的 visible/published timestamp 必須 <= `as_of`。
- missing timestamp → 排除並計數（fail-closed）。
- invalid/unparsable timestamp → 排除並計數。
- future timestamp（相對 as_of）→ 排除並計數。
- outcome `T+N` 不得進入 features（只作為 label）。

### FR-3: 同日多 Family 保留

- 同一 `as_of` 日期的 FNG/replay/onchain 以多 rows 或明確 family aggregation 全部保留。
- 不得因字典 key 碰撞而互相覆寫。
- FNG market-wide 不因多幣展開而虛增獨立來源。
- Blockchain.com charts 保持 BTC-only scope。

### FR-4: 確定性排序與可溯源 ID

- 輸出 JSONL 行順序為確定性（依 as_of + source + coin 排序）。
- 每筆 sample 有穩定 `sample_id`（`sha256(coin:as_of:source:direction:horizon)[:16]`）。
- `lineage_hash` 為所有輸入 artifact bytes 的 composite SHA-256。

### FR-5: Cutoff 語義

- `training_cutoff` 採 UTC `YYYY-MM-DD` inclusive。
- 只納入 `as_of` <= cutoff 的 samples。

## 非功能需求

- **NFR-1: 零第三方依賴** — 純 stdlib + json/csv/hashlib。
- **NFR-2: 安全需 CISO 審查** — PR 必須有 harper（CISO）review。
- **NFR-3: 輸入上限** — 單檔 32MB、batch 256MB、replay 10,000 files。
- **NFR-4: 向後相容** — 已產出的合法 JSONL artifacts 可被新版讀回。

## 驗收條件

1. `grep -rn "eval\|exec" scripts/build_historical_samples.py` 回傳空。
2. Hostile eval payload 測試 → 排除並報錯，不執行。
3. Future evidence timestamp 測試 → 排除並計數。
4. 同日 2+ families 測試 → 全部保留為獨立 rows。
5. 單元測試 + subprocess CLI 測試全通過。
6. Named reviewer + harper CISO + Eye + /codex-review + full pre-push 全通過。
