# 設計：Sample Contract 安全性、PIT 強化與同日多來源

> Issue: #750
> PR: #754 (merged to develop)

## 架構決策

### AD-1: 輸入解析層——拒絕任意程式碼執行

```
Input files (JSONL/CSV)
  → _read_stable_bytes()         # 防 symlink/TOCTOU/size-bomb
  → json.loads() per line        # 純 JSON，不 eval
  → validate_schema()            # 欄位型別白名單驗證
  → reject / count malformed
```

安全哲學：
- **永不** 使用 `eval()`/`exec()`/`compile()` 處理外部資料。
- 使用 `os.O_NOFOLLOW` + `lstat()`/`fstat()` identity 檢查防止 TOCTOU。
- 檔案讀取後驗證 inode identity 一致性。

### AD-2: PIT Gate 實作

```python
def _validate_pit(evidence_ts: datetime, as_of: datetime) -> bool:
    """Return True only if evidence timestamp is non-future relative to as_of."""
    return evidence_ts <= as_of
```

每筆 evidence 經過三道檢查：
1. `_utc_datetime(value)` — 解析為 UTC datetime，失敗 → None → 排除
2. 值為 None → 計入 `counters["missing_timestamp"]`
3. 值 > as_of → 計入 `counters["future_evidence"]`

outcome_observed_at 必須 > as_of（因為 T+N 天後才能觀測），但 evidence 來源 timestamp 必須 <= as_of。

### AD-3: 同日多 Family 資料結構

舊實作用 `dict[date_str, sample]` 做暫存 → 同日衝突覆寫。

修正後：
```python
# samples: list[dict]，每筆獨立累加
# 排序：sorted(samples, key=lambda s: (s["as_of"], s["source"], s["coin"]))
```

FNG 約束：
- `_SOURCE_IDENTITY["alternative-me-fng"]` → scope="market-wide"
- 同一 published_at 只產一筆（coin=BTC 作代表），不因幣種展開

Blockchain.com 約束：
- scope="per-coin"，只有 BTC 有資料

### AD-4: 檔案讀取安全層

```python
def _read_stable_bytes(path: Path, *, limit: int | None = None) -> bytes:
    # 1. lstat → 驗證是 regular file，非 symlink
    # 2. os.open(O_RDONLY | O_NOFOLLOW)
    # 3. fstat → 驗證 (dev, ino) 與 lstat 一致
    # 4. 驗證 size <= limit
    # 5. 讀取全部 bytes
    # 6. 讀後再 fstat → 驗證 mtime 未變
```

### AD-5: Lineage Hash

```python
def _lineage_hash_bytes(blobs: list[tuple[Path, bytes]]) -> str:
    """Compute SHA-256 over sorted (path, content) pairs."""
    h = hashlib.sha256()
    for path, data in sorted(blobs, key=lambda x: str(x[0])):
        h.update(str(path).encode())
        h.update(data)
    return h.hexdigest()
```

## 測試策略

### 單元測試 `tests/test_build_historical_samples.py`

| 案例 | 驗證目標 |
|------|----------|
| hostile eval payload `"__import__('os').system('rm -rf /')"` | 排除不執行 |
| future evidence timestamp (as_of=2026-01-01, evidence=2026-01-02) | 排除並計數 |
| missing timestamp | 排除並計數 |
| 同日 FNG + OHLCV + blockchain → 3 rows | 不覆寫 |
| FNG 多幣同日只產一筆 market-wide | 不虛增 |
| deterministic ordering | 多次執行結果相同 |
| sample_id stability | 相同輸入 → 相同 ID |
| TOCTOU: symlink input | ReplayInputError |
| oversized input | ReplayInputError |

### CLI 整合測試（subprocess）

- `python3 scripts/build_historical_samples.py --cutoff 2026-01-01 ...` 正常輸出
- 驗證 exit code、stdout summary、output JSONL 格式

## 安全考量

- 本修改需 CISO (harper) 審查：涉及 eval 移除與輸入驗證。
- 預防向量：code injection、path traversal、symlink following、TOCTOU race。
- 所有拒絕情況記入 counters 供審計。
