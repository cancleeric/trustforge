# 實作任務：Sample Contract 安全性、PIT 強化與同日多來源

> Issue: #750
> PR: #754 (merged to develop)

## Task 1: 移除 eval/exec，實作安全輸入解析

- [x] 搜尋並移除所有 `eval()`/`exec()` 呼叫
- [x] 實作 `_read_stable_bytes(path, *, limit)` 安全讀檔函式
  - lstat 驗證 regular file
  - O_RDONLY | O_NOFOLLOW open
  - fstat identity 比對
  - size limit 檢查
  - 讀後 mtime 驗證
- [x] 實作 `_decode_utf8(data, counters)` 解碼層
- [x] JSON/JSONL 解析只用 `json.loads()`，malformed 行計入 counters
- [x] 設定安全上限常數：`_MAX_INPUT_BYTES=32MB`, `_MAX_REPLAY_FILES=10000`, `_MAX_REPLAY_TOTAL_BYTES=256MB`

## Task 2: 實作嚴格 PIT Gate

- [x] 實作 `_utc_datetime(value)` → `datetime | None`
- [x] 實作 `_date_cutoff(value)` → 驗證 UTC YYYY-MM-DD
- [x] 在 `load_fng_records` / `_load_replay_snapshots` 中加入 timestamp 驗證
- [x] evidence timestamp > as_of → 排除並計入 `counters["future_evidence"]`
- [x] missing/invalid timestamp → 排除並計入 `counters["missing_timestamp"]`
- [x] outcome T+N 嚴格只做 label，不進 feature 計算

## Task 3: 修正同日多 Family 保留

- [x] 移除舊 dict-key 暫存邏輯
- [x] 改用 list 累加，每筆 evidence 獨立 append
- [x] FNG market-wide 只產一筆 sample（coin=BTC），不因幣種展開
- [x] Blockchain.com 保持 BTC-only scope
- [x] 確定性排序：`sorted(samples, key=(as_of, source, coin))`

## Task 4: 穩定 sample_id 與 lineage hash

- [x] `_sample_id(sample)` → `sha256(coin:as_of:source:direction:horizon)[:16]`
- [x] `_lineage_hash_bytes(blobs)` → sorted path+content 的 composite SHA-256
- [x] `lineage_hash(*file_paths)` → 對外介面

## Task 5: 更新 contract 文件

- [x] 更新 `docs/contracts/historical-sample-contract.md` 反映新欄位語義
- [x] 加入 PIT gate 章節說明
- [x] 加入安全約束章節

## Task 6: 新增測試

- [x] `tests/test_build_historical_samples.py` — 單元測試
  - hostile eval payload → 排除不執行
  - future evidence → 排除計數
  - missing timestamp → 排除計數
  - 同日 2+ families → 全保留
  - FNG 不虛增
  - deterministic ordering
  - sample_id 穩定性
  - symlink → ReplayInputError
  - oversized → ReplayInputError
- [x] subprocess CLI 整合測試 — 驗證 exit code 與 output 格式

## Task 7: Review gates

- [x] Named reviewer requested
- [x] Harper (CISO) security review
- [x] Eye scan (0/0)
- [x] /codex-review APPROVE
- [x] Full pre-push PASS (4763 backend, 459 frontend, 24/24 QA)
