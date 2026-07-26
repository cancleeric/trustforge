"""#104+#113：ops evidence 驗證測試。

驗證：
1. CloudWatch alarm 設定的一致性（deploy/put_dedup_alarm.sh 參數預設值）。
2. nginx 四份設定檔的 X-Real-IP 設定完整性（不可偽造的 $remote_addr）。
3. Python _resolve_client_ip 在 TRUST_PROXY 開/關下的行為。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"


# ── CloudWatch alarm ──────────────────────────────────────────────

def test_put_dedup_alarm_script_exists():
    assert (DEPLOY_DIR / "put_dedup_alarm.sh").is_file(), \
        "put_dedup_alarm.sh 必須存在"


def test_cloudwatch_alarm_script_defaults():
    """驗證 put_dedup_alarm.sh 的預設值與 cloudwatch_metrics.py 一致。"""
    script = (DEPLOY_DIR / "put_dedup_alarm.sh").read_text()

    # 預設 threshold = 5（對應 web.py _DEDUP_PREP_FAILURE_ALERT_THRESHOLD）
    assert 'THRESHOLD:-5}' in script or \
           'TRUSTFORGE_DEDUP_ALARM_THRESHOLD:-5}' in script, \
           "alarm threshold 預設應為 5"

    # 預設 namespace = TrustForge（對應 cloudwatch_metrics.py DEFAULT_NAMESPACE）
    assert 'TRUSTFORGE_CW_NAMESPACE:-TrustForge}' in script, \
           "CW namespace 預設應為 TrustForge"

    # 預設 metric = DedupFailOpenRecentFailures（對應 cloudwatch_metrics.py METRIC_NAME）
    assert 'TRUSTFORGE_CW_METRIC:-DedupFailOpenRecentFailures}' in script, \
           "metric name 預設應為 DedupFailOpenRecentFailures"

    # 預設 period = 300
    assert 'TRUSTFORGE_DEDUP_ALARM_PERIOD:-300}' in script, \
           "period 預設應為 300 秒"

    # 預設 evaluation-periods = 1
    assert 'TRUSTFORGE_DEDUP_ALARM_EVAL_PERIODS:-1}' in script, \
           "evaluation periods 預設應為 1"

    # 預設 treat-missing-data = notBreaching
    assert 'notBreaching' in script, \
           "treat-missing-data 應設為 notBreaching"

    # 有 put-metric-filter（log-based backup alarm）
    assert 'put-metric-filter' in script, \
           "必須有 log metric filter（雙路告警的第二條路）"

    # 有兩個 put-metric-alarm 呼叫（數值 + log filter）
    assert script.count('put-metric-alarm') >= 2, \
           "應至少有 2 個 alarm（數值指標 + log filter）"


def test_cloudwatch_metrics_module_defaults():
    """驗證 cloudwatch_metrics.py 的常數與 alarm 腳本對齊。"""
    import trustforge.cloudwatch_metrics as cwm

    assert cwm.DEFAULT_NAMESPACE == "TrustForge"
    assert cwm.METRIC_NAME == "DedupFailOpenRecentFailures"
    assert cwm.BUDGET_GUARD_BACKEND_DOWN_METRIC == "BudgetGuardMultiInstanceProtectionDisabled"


# ── nginx X-Real-IP ──────────────────────────────────────────────

NGINX_CONFS = [
    "nginx.conf",
    "nginx-react-http.conf",
    "nginx-legacy.conf",
    "nginx-legacy-tls.conf",
]


@pytest.mark.parametrize("conf_name", NGINX_CONFS)
def test_nginx_x_real_ip_is_remote_addr_not_transparent(conf_name):
    """所有 nginx conf 的 X-Real-IP 必須設為 $remote_addr（不可偽造），
    不能透傳客戶端自帶的 header。"""
    conf_path = DEPLOY_DIR / conf_name
    if not conf_path.is_file():
        pytest.skip(f"{conf_name} 不存在")

    content = conf_path.read_text()

    # 每個 proxy_set_header X-Real-IP 後面必須是 $remote_addr
    for m in re.finditer(r'proxy_set_header\s+X-Real-IP\s+(\S+)', content):
        value = m.group(1).rstrip(";")
        assert value == "$remote_addr", \
            f"{conf_name} 的 X-Real-IP 必須設為 $remote_addr，不是 {value}"

    # 必須至少有一行 proxy_set_header X-Real-IP（除非是技術封鎖的 location）
    x_real_ip_count = len(re.findall(r'proxy_set_header\s+X-Real-IP', content))
    assert x_real_ip_count >= 1, \
        f"{conf_name} 必須至少有一處 proxy_set_header X-Real-IP"


@pytest.mark.parametrize("conf_name", NGINX_CONFS)
def test_nginx_upstream_has_backup(conf_name):
    """所有 nginx conf 必須有 backup backend 供 zero-downtime restart。"""
    conf_path = DEPLOY_DIR / conf_name
    if not conf_path.is_file():
        pytest.skip(f"{conf_name} 不存在")

    content = conf_path.read_text()
    assert "backup" in content, \
        f"{conf_name} 必須包含 backup upstream server"


def test_nginx_tls_conf_admin_x_forwarded_for_overwrite():
    """nginx.conf（TLS 版）的 admin location 必須同時覆寫 X-Forwarded-For。
    _resolve_client_ip 在 X-Real-IP 缺席時會 fallback 讀 XFF 第一段，
    若只覆寫 X-Real-IP 而 XFF 仍透傳，攻擊者可偽造 XFF 繞過。"""
    conf = (DEPLOY_DIR / "nginx.conf").read_text()

    admin_block_match = re.search(
        r'location /api/admin/ \{.*?\n\s+\}',
        conf, re.DOTALL
    )
    if not admin_block_match:
        # 若全文沒有 /api/admin/ location，可能是 HTTP-only conf，pass
        # 但 nginx.conf (TLS) 應該有
        if "location /api/admin/" in conf:
            pytest.fail("nginx.conf 的 /api/admin/ block 無法解析")
        return

    admin_block = admin_block_match.group(0)
    assert 'proxy_set_header X-Real-IP $remote_addr' in admin_block, \
        "admin block 必須覆寫 X-Real-IP 為 $remote_addr"
    assert 'proxy_set_header X-Forwarded-For $remote_addr' in admin_block, \
        "admin block 必須同時覆寫 X-Forwarded-For 為 $remote_addr（防 XFF 偽造 fallback）"


def test_nginx_http_conf_admin_blocked():
    """nginx-react-http.conf（HTTP-only）必須技術封鎖 /api/admin/（return 404）。"""
    conf = (DEPLOY_DIR / "nginx-react-http.conf").read_text()
    assert "location ^~ /api/admin/" in conf, \
        "HTTP-only conf 必須有 /api/admin/ location block"
    assert "return 404" in conf, \
        "HTTP-only conf 的 /api/admin/ 必須 return 404"


# ── Python _resolve_client_ip ────────────────────────────────────

def test_resolve_client_ip_trust_proxy_on_prefers_x_real_ip(monkeypatch):
    """TRUST_PROXY=1 時，優先取 X-Real-IP，不取直連 IP。"""
    monkeypatch.setattr(
        "trustforge.web.TRUST_PROXY", True
    )
    from trustforge.web import _resolve_client_ip

    class FakeHeaders:
        def get(self, key, default=None):
            return {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8, 9.9.9.9"}.get(key, default)

    result = _resolve_client_ip("127.0.0.1", FakeHeaders())
    assert result == "1.2.3.4", "TRUST_PROXY 開時應取 X-Real-IP"


def test_resolve_client_ip_trust_proxy_off_uses_direct_ip(monkeypatch):
    """TRUST_PROXY=0（預設）時，不回退讀 header，直接用直連 IP。"""
    monkeypatch.setattr(
        "trustforge.web.TRUST_PROXY", False
    )
    from trustforge.web import _resolve_client_ip

    class FakeHeaders:
        def get(self, key, default=None):
            return {"X-Real-IP": "1.2.3.4"}.get(key, default)

    result = _resolve_client_ip("10.0.0.1", FakeHeaders())
    assert result == "10.0.0.1", "TRUST_PROXY 關時應取直連 IP，忽略 X-Real-IP"


def test_resolve_client_ip_trust_proxy_on_no_x_real_ip_falls_to_xff(monkeypatch):
    """TRUST_PROXY=1、無 X-Real-IP → 退回 X-Forwarded-For 最左段。"""
    monkeypatch.setattr(
        "trustforge.web.TRUST_PROXY", True
    )
    from trustforge.web import _resolve_client_ip

    class FakeHeaders:
        def get(self, key, default=None):
            return {"X-Forwarded-For": "5.6.7.8, 9.9.9.9"}.get(key, default)

    result = _resolve_client_ip("127.0.0.1", FakeHeaders())
    assert result == "5.6.7.8", "應退回 X-Forwarded-For 最左段"


def test_resolve_client_ip_empty_x_real_ip_falls_to_xff(monkeypatch):
    """TRUST_PROXY=1、X-Real-IP 為空字串 → 跳過，退回 X-Forwarded-For。"""
    monkeypatch.setattr(
        "trustforge.web.TRUST_PROXY", True
    )
    from trustforge.web import _resolve_client_ip

    class FakeHeaders:
        def get(self, key, default=None):
            return {"X-Real-IP": "", "X-Forwarded-For": "9.9.9.9"}.get(key, default)

    result = _resolve_client_ip("127.0.0.1", FakeHeaders())
    assert result == "9.9.9.9", "空 X-Real-IP 應退回 X-Forwarded-For"


# ── 雙指標獨立性 ──────────────────────────────────────────────────

def test_budget_guard_metric_not_gated_by_cw_metrics_env():
    """BudgetGuardMultiInstanceProtectionDisabled 指標不受 TRUSTFORGE_CW_METRICS opt-in 限制。
    （它是降級警報，不是觀測旁路）"""
    import trustforge.cloudwatch_metrics as cwm
    # 這個 metric 的 emit 函式刻意略過 metrics_enabled() 檢查
    # 驗證 emit_budget_guard_backend_down 存在且不讀 TRUSTFORGE_CW_METRICS
    import inspect
    src = inspect.getsource(cwm.emit_budget_guard_backend_down)
    body_lines = src.split('"""')[-1]  # 取 docstring 之後的函式主體
    assert "metrics_enabled" not in body_lines, \
        "BudgetGuard 降級指標主體不應該呼叫 metrics_enabled() 受 TRUSTFORGE_CW_METRICS opt-in 限制"
    # docstring 提及 TRUSTFORGE_CW_METRICS 屬正常（說明它不受此限制），只驗主體不檢查
