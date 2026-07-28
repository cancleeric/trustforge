# Multi-angle Backend 實作任務

## Tasks

- [x] 1. analysis_flow.py: 新增 `submit_multi_angle(coin, question, locale)` 方法
- [x] 2. analysis_flow.py: 新增 `multi_angle_status(coin, snapshot_id?)` 方法
- [x] 3. analysis_flow.py: 新增 `_maybe_trigger_synthesis(snapshot_id, coin)` 方法
- [x] 4. analysis_flow.py: 在 `_stage_report_delivery` COMMIT 後加 fail-soft synthesis 觸發
- [x] 5. web.py: 新增 `_handle_api_multi_angle_get(qs)` handler
- [x] 6. web.py: 新增 `_handle_api_multi_angle_post(headers, rfile, client_ip)` handler
- [x] 7. web.py: do_GET 加 `/api/multi-angle` route
- [x] 8. web.py: do_POST 加 `/api/multi-angle` route
- [x] 9. 建立 `tests/test_multi_angle_flow.py` 整合測試（9 tests passed）
- [x] 10. 確認所有測試通過、commit
