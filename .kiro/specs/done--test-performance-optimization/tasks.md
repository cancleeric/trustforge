# Tasks: 測試效能優化

## Task 1: 耗時分析報告 ✅
- [x] 跑 `pytest --durations=50` 取得最慢測試清單
- [x] 分類原因（sleep / coverage overhead / subprocess）
- [x] 結果：137s 中 46s 是 coverage，27.7s 是 sleep，其餘是計算

## Task 2: Coverage 配置優化 ✅
- [x] 加 `[tool.coverage.run] branch = false` 省分支追蹤開銷
- [x] 加 omit 排除 tests/ 和 cli.py
- [x] 效果：137s → 95s（-31%）

## Task 3: 修正 sleep 瓶頸 ✅
- [x] test_json_api: leader timeout 5.0→0.5（省 5.7s）
- [x] test_home_overview: stall 1.5→0.15, stall 0.3→0.05（省 1.5s）
- [x] test_module_telemetry: 23 個 sleep 0.5→0.05（省 ~10s）
- [x] test_d25_worstcase_lease: TTL 1→0.05（省 1s）
- [x] test_shadow_runtime: slow_kernel 2→0.9, hung 1→0.2
- [x] 效果：95s → 83s（再 -12s）

## Task 4: pytest-xdist 並行化 ✅
- [x] 加 pytest-xdist 到 dev dependencies
- [x] pre-push hook 加 `-n auto`
- [x] 效果：83s → **18s**（-78%）

## Task 5: 最終驗證 ✅
- [x] 全量測試 18s ✅（目標 <60s）
- [x] 覆蓋率 84.31% >= 75% ✅
- [x] 5008 tests, 0 failed ✅

## 總結

| 階段 | 時間 | 改善 |
|------|------|------|
| 原始 | 137s | — |
| coverage 優化 | 95s | -31% |
| sleep 修正 | 83s | -39% |
| xdist 並行 | **18s** | **-87%** |
