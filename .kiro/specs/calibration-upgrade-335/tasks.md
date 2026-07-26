# Tasks

## Task 1: `src/trustforge/calibration_runner.py`
- load_predictions()
- compare_predictions()（T+1/T+7/T+14）
- calculate_calibration_error()（5 bins）
- format_replay_report()

## Task 2: CLI `calibrate` 子命令
- `--coin` / `--all` / `--data-dir` / `--training-dir`

## Task 3: 測試
- tests/test_calibration_runner.py
- 含：正確 hit 判定、bin 計算、edge case

## Task 4: 整合驗證
- 跑一次 calibrate → diagnose → 確認 proposal 產出
