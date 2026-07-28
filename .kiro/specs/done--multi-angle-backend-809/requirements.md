# Multi-angle 後端入口、Synthesis 觸發與 API Endpoint

> Issue: #809
> 依賴: #808 (multi_angle.py 資料契約，已完成)

## 需求

在 `analysis_flow.py` 新增 `submit_multi_angle()` 入口（共用一個 snapshot 同時跑五角度），
在 `_stage_report_delivery` 結尾新增 synthesis 觸發，
在 `web.py` 新增 `/api/multi-angle` endpoint（GET 讀取 + POST 觸發）。

## 功能需求

### FR-1: submit_multi_angle(coin, question, locale)
- 驗證 coin ∈ COIN_POOL
- 呼叫一次 `create_snapshot(coin, query=question)`
- 為五個 MODES 各 `enqueue_job(snapshot_id, mode, template.format(coin=coin), origin="manual", locale=locale)`
- 記錄 lineage 事件 `multi_angle_submitted`
- 回傳 `{snapshot_id, job_ids: {mode: job_id}, coin}`

### FR-2: multi_angle_status(coin, snapshot_id?)
- 從 `analysis_results` 查詢 mode='multi_angle' 的最新結果
- 支援指定 snapshot_id 或取最新
- readonly 模式安全（store 不存在回 None）

### FR-3: _maybe_trigger_synthesis(snapshot_id, coin)
- 在 `_stage_report_delivery` COMMIT 後呼叫（fail-soft）
- 檢查同 snapshot 五角度是否全部有 result
- 避免重複觸發（已存在 mode='multi_angle' 就跳過）
- 呼叫 `synthesize_angles()` 產出 MultiAngleReport
- 結果存入 `analysis_results`（mode='multi_angle'）
- 記錄 lineage 事件 `multi_angle_synthesized`

### FR-4: GET /api/multi-angle?coin=BTC[&snapshot_id=xxx]
- 回傳 `{ok: true, data: {multi_angle: MultiAngleReport | null}}`
- coin 必填，snapshot_id 選填
- 查無結果回 200 + null（不是 404）

### FR-5: POST /api/multi-angle {coin, question?, locale?}
- 觸發 submit_multi_angle
- 回傳 `{ok: true, data: {snapshot_id, job_ids, coin}}`
- coin 必填，question 選填（預設模板），locale 選填（預設 zh-Hant）

## 非功能需求

### NFR-1: Fail-soft
- synthesis 觸發失敗不影響正常 report_delivery 完成
- API 錯誤回 502 + 有意義訊息

### NFR-2: 零新依賴
- 只用既有 analysis_flow 架構 + multi_angle.py

### NFR-3: 向後相容
- 現有單角度流程零修改行為
- 新的 synthesis 結果存在既有 analysis_results 表（mode='multi_angle'）

## 約束

- AnalysisFlow 用 `with ... as flow` context manager pattern
- web.py 的 readonly=True 給 GET，writable 給 POST
- POST 走既有的 `_json_envelope_ok` / `_json_envelope_err` 信封格式
