"""#104：dedup fail-open CloudWatch 數值監控測試。

驗證：
- `emit_dedup_fail_open_metric` 在 `TRUSTFORGE_CW_METRICS` 未啟用時是 no-op
  （零 AWS 呼叫、不建 client）。
- 啟用後，每次 dedup 準備失敗都會把「滑動視窗內次數」(`recent_failures`) 以
  正確的 `MetricName` / `Value` / `Unit` / `Dimensions` 送進 `put_metric_data`。
- 頻率超過門檻（fail-open 頻率過高）時，送出的 `Value` 達到告警門檻值，對應
  `deploy/put_dedup_alarm.sh` 建的 CloudWatch Alarm 會觸發。
- 上報失敗（client 炸）絕不 raise，只回 False、呼叫端不受影響。
- web `_record_dedup_prep_failure` 在啟用指標時確實觸發 `emit`（端到端整合）。
"""
from __future__ import annotations

import pytest

from trustforge import cloudwatch_metrics
from trustforge.cloudwatch_metrics import emit_dedup_fail_open_metric


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_CW_METRICS", raising=False)
    cloudwatch_metrics.set_client_for_tests(None)
    yield
    cloudwatch_metrics.set_client_for_tests(None)


class _FakeCWClient:
    def __init__(self):
        self.calls: list[dict] = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)


def test_disabled_is_noop_and_no_aws_call(monkeypatch):
    """未啟用時不應建立 client、不發任何 AWS 呼叫。"""
    made_client = {"v": False}

    def _should_not_run(*a, **k):
        made_client["v"] = True
        raise AssertionError("不該建立 CloudWatch client")

    monkeypatch.setattr(cloudwatch_metrics, "_get_or_create_client", _should_not_run)
    assert emit_dedup_fail_open_metric(7) is True
    assert made_client["v"] is False


def test_enabled_emits_correct_metric(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_CW_METRICS", "1")
    fake = _FakeCWClient()
    cloudwatch_metrics.set_client_for_tests(fake)

    assert emit_dedup_fail_open_metric(3) is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["Namespace"] == "TrustForge"
    md = call["MetricData"][0]
    assert md["MetricName"] == "DedupFailOpenRecentFailures"
    assert md["Value"] == 3
    assert md["Unit"] == "Count"
    assert md["Dimensions"] == [{"Name": "Service", "Value": "trustforge"}]


def test_negative_count_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_CW_METRICS", "1")
    fake = _FakeCWClient()
    cloudwatch_metrics.set_client_for_tests(fake)
    emit_dedup_fail_open_metric(-5)
    assert fake.calls[0]["MetricData"][0]["Value"] == 0


def test_failure_above_threshold_value_for_alarm(monkeypatch):
    """頻率超過告警門檻（5）時，送出的 Value 達到門檻，對應 Alarm 觸發。"""
    monkeypatch.setenv("TRUSTFORGE_CW_METRICS", "1")
    fake = _FakeCWClient()
    cloudwatch_metrics.set_client_for_tests(fake)
    emit_dedup_fail_open_metric(6)  # > _DEDUP_PREP_FAILURE_ALERT_THRESHOLD(5)
    assert fake.calls[0]["MetricData"][0]["Value"] == 6


def test_emit_failure_never_raises(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_CW_METRICS", "1")

    class _Boom:
        def put_metric_data(self, **kwargs):
            raise RuntimeError("network down")

    cloudwatch_metrics.set_client_for_tests(_Boom())
    # 不應 raise，回 False
    assert emit_dedup_fail_open_metric(2) is False


def test_web_record_dedup_failure_emits_metric(monkeypatch):
    """端到端：web `_record_dedup_prep_failure` 在啟用指標時真的觸發 emit。"""
    monkeypatch.setenv("TRUSTFORGE_CW_METRICS", "1")
    fake = _FakeCWClient()
    cloudwatch_metrics.set_client_for_tests(fake)

    import trustforge.web as web

    # 觸發 6 次失敗（超過門檻 5），滑動視窗內計數應達 6 並送出。
    for _ in range(6):
        web._record_dedup_prep_failure("coin_key")

    assert fake.calls, "應至少送出一次指標"
    last = fake.calls[-1]["MetricData"][0]
    assert last["Value"] >= 5
