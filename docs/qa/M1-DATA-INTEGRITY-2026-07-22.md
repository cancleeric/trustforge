# Milestone 1 — Training Data Repair and OHLCV Integrity

Date: 2026-07-22 (Asia/Taipei)

## Scope and repair evidence

- Scope was limited to `data/training/BTC.jsonl`, `data/training/ETH.jsonl`, the read-only integrity tool, its checksum manifest, regression tests, and this QA record.
- Before repair, `git diff --numstat` showed exactly 75 added BTC rows and 15 added ETH rows.
- The working-tree content and uncommitted Git diff were manually cross-checked before editing. Both views contained the exact locked counts; every matched row had `generated_at` beginning `2026-07-22T04:` and the expected coin.
- The 90 confirmed rows were manually removed with `apply_patch`. After repair, both training files are byte-identical to `HEAD` and absent from `git diff`.
- The shipped audit tool is strictly read-only. It has no training-data cleanup, Git subprocess, or file-writing capability.

## OHLCV acceptance evidence

`python3 scripts/audit_data_integrity.py` passed for BTC, ETH, SOL, BNB, and XRP:

- exactly 1,826 rows per coin;
- SHA-256 matches the versioned `data/ohlcv_checksums.json` manifest, which permits exactly the five official CSV files; digest and parser share the same immutable bytes snapshot;
- manifest, metadata, and CSV inputs are read through `safe_fs.read_regular_file`, rejecting symlinks, non-regular files, and files above their 64 KiB / 256 KiB / 2 MiB limits;
- unique, sorted, consecutive UTC dates from 2021-06-01 through 2026-05-31;
- file, symbol, pair, row count, date range, interval, time basis, USDT unit, timezone-aware generation time, and official source metadata consistent with `data/dataset_metadata.json`;
- exactly six non-empty fields per CSV row, strict `YYYY-MM-DD` dates, positive finite OHLC values, `low <= open/close <= high`, and non-negative finite volume.

## Commands and results

```text
python3 scripts/audit_data_integrity.py
PASS: checksum schema 1.0.0 and five-coin OHLCV audit status ok

pytest tests/test_data_integrity.py --no-cov
PASS: 23 passed (checksum snapshot binding, safe-file symlink/size limits, UTF-8, ragged rows, metadata/date rejection, invariants, and repository coverage)

git diff --check
PASS
```

No database/migration, external service, live ModelHub, secret, or Docker action was performed.
