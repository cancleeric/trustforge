# Pytest Benchmark Inventory

Issue #479 uses a read-only wrapper to make pytest runtime claims reproducible without
changing CI, coverage thresholds, pytest configuration, or deploy workflows.

Run from the repository root:

```bash
python scripts/pytest_benchmark_inventory.py
```

The wrapper performs one collection pass, one warmup run, and three measured runs. It writes
JSON and Markdown reports under `artifacts/pytest-benchmark-inventory/` with:

- Python and pytest versions
- collection return code, wall time, collected count, and node IDs
- warmup return code
- measured run pass/fail/skip/error/xfail/xpass/deselected counts
- measured median and min/max wall time
- pytest `--durations=50 --durations-min=0` rows from measured runs

Extra pytest arguments may be passed after `--`, for example:

```bash
python scripts/pytest_benchmark_inventory.py -- tests/test_pytest_marker_taxonomy.py --no-cov
```

The process exits non-zero if collection, warmup, or any measured pytest run exits non-zero.
Reports redact repository/home paths and secret-like output lines before writing artifacts.
