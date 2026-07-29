# Ops Evidence — CloudWatch Alarm (#104)

> 決賽提交用：CloudWatch 告警與指標上報證據摘要。

---

## 1. 告警一覽

| Alarm 名稱 | 指標 | 觸發條件 | 通知 |
|---|---|---|---|
| `trustforge-dedup-fail-open` | `DedupFailOpenRecentFailures` (數值) | Max ≥ 5，持續 300 秒 | SNS（若有設定） |
| `trustforge-dedup-alert-log` | `DedupFailOpenAlertLogCount` (log filter) | Sum ≥ 1，持續 300 秒 | SNS（若有設定） |

## 2. 架構

```
web.py (_record_dedup_prep_failure)
  ├─ 累積滑動視窗失敗次數
  └─ 觸發 cloudwatch_metrics.emit_dedup_fail_open_metric(recent_failures)
       └─ put_metric_data("DedupFailOpenRecentFailures", Value=recent_failures)

budget_guard.py (_backend_unavailable_alert)
  └─ 觸發 cloudwatch_metrics.emit_budget_guard_backend_down()
       └─ put_metric_data("BudgetGuardMultiInstanceProtectionDisabled", Value=1)
```

- **指標 Namespace**：`TrustForge`
- **指標 Dimension**：`Service=trustforge`
- **指標上報**：opt-in（`TRUSTFORGE_CW_METRICS=1`），不上報時為 no-op（零 AWS 呼叫）

## 3. 雙路告警設計

### 3.1 數值指標告警 (`trustforge-dedup-fail-open`)
- **指標**：`DedupFailOpenRecentFailures`
- **來源**：`src/trustforge/cloudwatch_metrics.py::emit_dedup_fail_open_metric()`
- **語意**：滑動視窗內 dedup 準備失敗次數（重複計費/去重失效）
- **觸發**：當 `recent_failures ≥ 5`（預設）→ CloudWatch Alarm 觸發
- **優點**：數值型指標可視覺化為線圖、閾值告警不依賴 log 解析
- **設定檔**：`deploy/put_dedup_alarm.sh`

### 3.2 Log filter 告警 (`trustforge-dedup-alert-log`)
- **指標**：`DedupFailOpenAlertLogCount`（由 CloudWatch Logs metric filter 產生）
- **Log pattern**：`"ALERT: TrustForge dedup"` 前綴
- **來源**：`web.py` 的 `_record_dedup_prep_failure` 在頻率達門檻時記 ERROR 級 ALERT log
- **觸發**：Sum ≥ 1（任一筆 ALERT log 出現即告警）
- **優點**：作為 log-based backup（不依賴數值指標上報是否成功）

### 3.3 Budget Guard 降級警報（額外）
- **指標**：`BudgetGuardMultiInstanceProtectionDisabled`
- **來源**：`budget_guard.py` 的 `_backend_unavailable_alert()`
- **語意**：多實例預留保護的 DynamoDB 後端不可用；admission 已 fail-closed 拒絕，不 fallback process-local
- **特性**：不受 `TRUSTFORGE_CW_METRICS` opt-in 限制（降級警報而非觀測旁路）

## 4. 部署指令

```bash
export TRUSTFORGE_DEDUP_ALARM_SNS="arn:aws:sns:us-east-1:795930814369:trustforge-alerts"
bash deploy/put_dedup_alarm.sh
```

無 SNS 時仍會建立 alarms（狀態可見，但無通知）。支援的環境變數：

| 變數 | 預設值 | 說明 |
|---|---|---|
| `REGION` | `us-east-1` | AWS region |
| `TRUSTFORGE_CW_NAMESPACE` | `TrustForge` | 指標 namespace |
| `TRUSTFORGE_DEDUP_ALARM_THRESHOLD` | `5` | 數值指標告警門檻 |
| `TRUSTFORGE_DEDUP_ALARM_PERIOD` | `300` | 評估週期（秒） |
| `TRUSTFORGE_DEDUP_ALARM_SNS` | 空 | SNS topic ARN |

## 5. 關鍵原始碼位置

| 檔案 | 內容 |
|---|---|
| `src/trustforge/cloudwatch_metrics.py` | CloudWatch 指標 emitter（雙指標：dedup fail-open + budget guard 降級） |
| `src/trustforge/web.py` L4808-L4813 | `_record_dedup_prep_failure` 觸發 emit |
| `src/trustforge/budget_guard.py` L633-L659 | budget guard 後端降級觸發 emit |
| `deploy/put_dedup_alarm.sh` | CloudWatch Alarm + metric filter 建立腳本 |
| `tests/test_cloudwatch_dedup_alarm.py` | 指標上報單元測試（6 條） |
| `tests/test_put_dedup_alarm.py` | alarm 腳本建構測試（3 條） |

## 6. 測試覆蓋

```
tests/test_cloudwatch_dedup_alarm.py:
  ✅ test_disabled_is_noop_and_no_aws_call — 未啟用＝零 AWS 呼叫
  ✅ test_enabled_emits_correct_metric — 啟用後正確送出 MetricName/Value/Unit/Dimension
  ✅ test_negative_count_clamped_to_zero — 負數 clamped 為 0
  ✅ test_failure_above_threshold_value_for_alarm — 頻率超門檻（6≥5）確定觸發告警
  ✅ test_emit_failure_never_raises — 上報失敗絕不 raise
  ✅ test_web_record_dedup_failure_emits_metric — 端到端整合測試
```
