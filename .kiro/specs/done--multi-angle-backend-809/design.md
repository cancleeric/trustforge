# Multi-angle Backend 設計文件

## 架構

```
POST /api/multi-angle {coin, question, locale}
       │
       ▼
AnalysisFlow.submit_multi_angle(coin, question, locale)
       │
       ├── create_snapshot(coin) → snapshot_id
       │
       ├── enqueue_job(snapshot_id, "risk", ...) → job_id
       ├── enqueue_job(snapshot_id, "sentiment", ...) → job_id
       ├── enqueue_job(snapshot_id, "fundamentals", ...) → job_id
       ├── enqueue_job(snapshot_id, "news", ...) → job_id
       └── enqueue_job(snapshot_id, "catalyst", ...) → job_id
               │
               ▼  (daemon workers 各自跑 5 stages)
       _stage_report_delivery  ← 每個 job 完成時
               │
               ▼
       _maybe_trigger_synthesis(snapshot_id, coin)
               │
               ├── 檢查五角度是否全完成
               ├── synthesize_angles() → MultiAngleReport
               └── INSERT analysis_results (mode='multi_angle')
                       │
                       ▼
GET /api/multi-angle?coin=BTC
       │
       ▼
AnalysisFlow.multi_angle_status(coin) → payload_json
```

## analysis_flow.py 修改

### submit_multi_angle

位置：在 `question_context()` 之前新增。

```python
def submit_multi_angle(self, coin: str, question: str, *,
                       locale: str = DEFAULT_NARRATIVE_LOCALE) -> dict[str, Any]:
    coin = coin.strip().upper()
    if coin not in COIN_POOL:
        raise ValueError(f"unsupported coin: {coin}")
    locale = normalize_locale(locale)
    snapshot_id = self.create_snapshot(coin, query=question)
    job_ids = {}
    for mode, (_qtype, template) in MODES.items():
        mode_question = template.format(coin=coin)
        self.register_question(coin, mode, mode_question, enqueue=False)
        job_id = self.enqueue_job(snapshot_id, mode, mode_question,
                                  origin="manual", locale=locale)
        job_ids[mode] = job_id
    self._append_lineage(
        "multi_angle_submitted", entity_type="multi_angle_run",
        entity_id=f"ma-{snapshot_id}", snapshot_id=snapshot_id,
        metadata={"coin": coin, "job_ids": job_ids, "locale": locale},
    )
    return {"snapshot_id": snapshot_id, "job_ids": job_ids, "coin": coin}
```

### _maybe_trigger_synthesis

位置：class 內部方法，在 `_stage_report_delivery` COMMIT 後 fail-soft 呼叫。

觸發條件：
1. 同 snapshot 五角度都有 analysis_results
2. 尚無 mode='multi_angle' 的 result（避免重複）

### multi_angle_status

位置：class 內部方法，readonly safe。

## web.py 修改

### Handler 函式

```python
def _handle_api_multi_angle_get(qs) -> tuple[int, str]
def _handle_api_multi_angle_post(headers, rfile, client_ip) -> tuple[int, str]
```

### Route 註冊

- do_GET: `if u.path == "/api/multi-angle":` → `_handle_api_multi_angle_get(qs)`
- do_POST: `if u.path == "/api/multi-angle":` → `_handle_api_multi_angle_post(...)`

### AnalysisFlow 使用模式

- GET: `AnalysisFlow(readonly=True)` — context manager
- POST: 需要寫入，使用 daemon 的共用 flow instance 或新建 writable instance

## 測試策略

`tests/test_multi_angle_flow.py`:
- submit_multi_angle 回傳五個 job_ids
- _maybe_trigger_synthesis 在四角度完成時不觸發
- _maybe_trigger_synthesis 在五角度完成時觸發且結果正確
- 重複觸發被 idempotency 擋住
- API GET 查無結果回 200 + null
- API GET 有結果回 200 + MultiAngleReport
