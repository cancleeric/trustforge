"""Lambda handler（Function URL 進入點）錯誤分支一致性回歸測試。

商業級一致性修（codex MEDIUM）：`lambda_handler.py` 的 400/429/502/404 HTML
錯誤分支原本是裸 `<p style='color:#c00'>`／裸 `<p>404</p>`，跟 EC2 `web.py`
（已改用 `web._render_error_card` 品牌卡 + 返回首頁）不一致——同一產品兩個
入口視覺不統一。本檔斷言 Lambda 入口的 HTML 錯誤分支已改走同一個
`_render_error_card`，且 JSON 端點（`/analyze.json` 成功路徑）不受影響。
"""
import html
import json
from urllib.parse import urlencode

import pytest

from trustforge import lambda_handler, lambda_provider_cache, web
from trustforge.comparison_contract import ComparisonReport, ComparisonRunResult
from trustforge.schema import Evidence, QuestionType


@pytest.fixture(autouse=True)
def _disable_real_provider_refresh(monkeypatch):
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: {
            name: ("cached", 1)
            for name in (
                "arkham-intel",
                "coinmarketcap-price",
                "etherscan-whale",
                "whale-alert",
            )
        },
    )


def test_noncompetition_analysis_does_not_refresh_providers(monkeypatch):
    calls = []
    def stop_pipeline(*args, **kwargs):
        raise RuntimeError("stop after refresh check")
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", None)
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda coin: calls.append(coin)
    )
    monkeypatch.setattr(web, "_do_analyze", stop_pipeline)
    response = lambda_handler.handler(_event("/analyze", {"coin": "BTC"}))
    assert response["statusCode"] == 502
    assert calls == []


@pytest.mark.parametrize(
    "query",
    [
        {"coin": "BTC,ETH", "type": "comparison", "live": "1"},
        {"coin": "BTC", "coin2": "ETH", "type": "comparison", "live": "1"},
    ],
)
def test_live_comparison_refreshes_both_coins(monkeypatch, query):
    calls = []
    def stop_pipeline(*args, **kwargs):
        raise RuntimeError("stop after refresh check")
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "valid-token")
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: calls.append(coin) or {
            name: ("cached", 1)
            for name in (
                "arkham-intel", "coinmarketcap-price", "etherscan-whale", "whale-alert"
            )
        },
    )
    monkeypatch.setattr(web, "_do_comparison", stop_pipeline)
    response = lambda_handler.handler(
        _event("/analyze.json", query, {"X-Live-Token": "valid-token"})
    )
    assert response["statusCode"] == 502
    assert calls == ["BTC", "ETH"]


def test_live_provider_refresh_failure_returns_safe_502(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "valid-token")
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: {"arkham-intel": ("failed:TimeoutError", 0)},
    )
    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"coin": "BTC", "live": "1"},
            {"X-Live-Token": "valid-token"},
        )
    )
    assert response["statusCode"] == 502
    assert "TimeoutError" not in response["body"]


def test_live_missing_coin_refreshes_btc_before_pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "valid-token")
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: calls.append(coin) or {
            name: ("cached", 1)
            for name in (
                "arkham-intel", "coinmarketcap-price", "etherscan-whale", "whale-alert"
            )
        },
    )
    monkeypatch.setattr(
        web, "_do_analyze", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop"))
    )
    response = lambda_handler.handler(
        _event("/analyze", {"live": "1"}, {"X-Live-Token": "valid-token"})
    )
    assert response["statusCode"] == 502
    assert calls == ["BTC"]


def test_live_invalid_coin_does_not_spend_provider_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "valid-token")
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda coin: calls.append(coin)
    )
    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"coin": "DOGE", "live": "1"},
            {"X-Live-Token": "valid-token"},
        )
    )
    assert response["statusCode"] == 400
    assert calls == []


def _event(path: str, qs: dict | None = None, headers: dict | None = None) -> dict:
    """組一個 Lambda Function URL（payload v2）事件。"""
    event = {
        "rawPath": path,
        "requestContext": {"http": {"sourceIp": "9.9.9.9"}},
    }
    if qs is not None:
        event["queryStringParameters"] = qs
    if headers is not None:
        event["headers"] = headers
    return event


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/"), ("GET", "/analyze"), ("POST", "/analyze.json"), ("GET", "/status")],
)
def test_competition_offline_hosted_rejects_non_allowlisted_routes(
    monkeypatch, method, path
):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "offline")
    event = _event(path)
    event["requestContext"]["http"].update({"method": method, "path": path})

    response = lambda_handler.handler(event)

    assert response["statusCode"] == 404
    assert "未開放該路由" in response["body"]


@pytest.mark.parametrize("path", ["/", "/healthz"])
def test_competition_offline_hosted_allows_get_root_and_health(monkeypatch, path):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "offline")
    event = _event(path)
    event["requestContext"]["http"].update({"method": "GET", "path": path})

    response = lambda_handler.handler(event)

    assert response["statusCode"] == 200


def test_competition_offline_landing_is_truthful_and_has_no_analysis_cta(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "offline")

    response = lambda_handler.handler(_event("/"))

    assert "AWS 離線唯讀展示" in response["body"]
    assert "不提供分析執行" in response["body"]
    assert "/analyze" not in response["body"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/"), ("POST", "/healthz"), ("POST", "/analyze"),
        ("POST", "/analyze.json"), ("GET", "/status"), ("GET", "/admin"),
        ("GET", "/unknown"),
    ],
)
def test_competition_live_rejects_non_allowlisted_routes(monkeypatch, method, path):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    event = _event(path)
    event["requestContext"]["http"].update({"method": method, "path": path})

    response = lambda_handler.handler(event)

    assert response["statusCode"] == 404
    assert "Live 端點未開放" in response["body"]


@pytest.mark.parametrize(
    ("qs", "headers"),
    [
        ({"coin": "BTC"}, {"X-Live-Token": "correct-token"}),
        ({"coin": "BTC", "live": "1"}, {}),
        ({"coin": "BTC", "live": "1"}, {"X-Live-Token": "wrong-token"}),
        ({"coin": "BTC", "live": "1", "token": "correct-token"}, {}),
    ],
)
def test_competition_live_analysis_fails_closed_without_exact_header(
    monkeypatch, qs, headers
):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    monkeypatch.setattr(
        web,
        "_do_analyze",
        lambda *args, **kwargs: pytest.fail("unauthorized request reached analysis"),
    )

    response = lambda_handler.handler(_event("/analyze", qs, headers))

    assert response["statusCode"] == 401
    assert "correct-token" not in response["body"]
    assert "wrong-token" not in response["body"]


@pytest.mark.parametrize("path", ["/analyze", "/analyze.json"])
def test_competition_live_allows_exact_header_and_live_flag(monkeypatch, path):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    monkeypatch.setattr(web, "_do_analyze", lambda *args, **kwargs: (object(), [], []))
    monkeypatch.setattr(web, "_render_report", lambda *args: "<html>live ok</html>")

    monkeypatch.setattr(
        web,
        "_build_analyze_json_payload",
        lambda *args: {"mode": "live", "report": {}, "evidence": []},
    )
    response = lambda_handler.handler(
        _event(
            path,
            {"coin": "BTC", "live": "1"},
            {"X-LIVE-TOKEN": "correct-token"},
        )
    )

    assert response["statusCode"] == 200
    if path == "/analyze":
        assert "live ok" in response["body"]
    else:
        assert response["headers"]["Content-Type"] == "application/json; charset=utf-8"


@pytest.mark.parametrize("path", ["/", "/healthz"])
def test_competition_live_public_routes_are_get_only(monkeypatch, path):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    event = _event(path)
    event["requestContext"]["http"].update({"method": "GET", "path": path})

    assert lambda_handler.handler(event)["statusCode"] == 200


def test_competition_function_requires_explicit_mode(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "competition-trustforge-team11-offline")
    monkeypatch.delenv("TRUSTFORGE_COMPETITION_MODE", raising=False)

    with pytest.raises(RuntimeError, match="explicit offline/live mode"):
        lambda_handler._competition_mode()


def test_noncompetition_function_preserves_existing_routes(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "trustforge-demo")
    monkeypatch.delenv("TRUSTFORGE_COMPETITION_MODE", raising=False)

    assert lambda_handler._competition_mode() is None


def test_noncompetition_analysis_never_refreshes_providers(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", None)
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: pytest.fail(f"noncompetition refresh attempted for {coin}"),
    )
    monkeypatch.setattr(web, "_do_analyze", lambda *args, **kwargs: (object(), [], []))
    monkeypatch.setattr(web, "_render_report", lambda *args: "ok")

    assert lambda_handler.handler(_event("/analyze", {"coin": "BTC"}))["statusCode"] == 200


@pytest.mark.parametrize("coin", ["", "DOGE", "X" * 2048])
def test_live_invalid_coin_never_refreshes_provider(monkeypatch, coin):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    calls = []
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda value: calls.append(value)
    )

    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"coin": coin, "live": "1"},
            {"X-Live-Token": "correct-token"},
        )
    )

    assert response["statusCode"] == 400
    assert calls == []


def test_live_invalid_comparison_second_coin_never_refreshes_provider(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    calls = []
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda value: calls.append(value)
    )

    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"type": "comparison", "coin": "BTC", "coin2": "DOGE", "live": "1"},
            {"X-Live-Token": "correct-token"},
        )
    )

    assert response["statusCode"] == 400
    assert calls == []


def test_live_invalid_question_type_never_refreshes_provider(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    calls = []
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda value: calls.append(value)
    )

    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"type": "bogus", "coin": "BTC", "live": "1"},
            {"X-Live-Token": "correct-token"},
        )
    )

    assert response["statusCode"] == 400
    assert calls == []


def test_live_rate_limited_request_never_refreshes_provider(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    calls = []
    monkeypatch.setattr(
        web,
        "_analyze_enforce_caller_rate_limit",
        lambda *_args: (_ for _ in ()).throw(web.TooManyRequests("limited")),
    )
    monkeypatch.setattr(
        lambda_provider_cache, "refresh_provider_cache", lambda value: calls.append(value)
    )

    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"coin": "BTC", "live": "1"},
            {"X-Live-Token": "correct-token"},
        )
    )

    assert response["statusCode"] == 429
    assert calls == []


def test_live_comparison_refreshes_both_coins_and_counts_rate_limit_once(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_COMPETITION_MODE", "live")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    refreshed = []
    limited = []
    monkeypatch.setattr(
        lambda_provider_cache,
        "refresh_provider_cache",
        lambda coin: refreshed.append(coin) or {
            name: ("cached", 1)
            for name in (
                "arkham-intel", "coinmarketcap-price", "etherscan-whale", "whale-alert"
            )
        },
    )
    monkeypatch.setattr(
        web, "_analyze_enforce_caller_rate_limit", lambda qs, ip: limited.append(ip)
    )

    def _comparison(*args, **kwargs):
        assert kwargs["client_ip"] == "9.9.9.9"
        assert kwargs["enforce_rate_limit"] is False
        raise ValueError("stop after admission")

    monkeypatch.setattr(web, "_do_comparison", _comparison)
    response = lambda_handler.handler(
        _event(
            "/analyze",
            {"type": "comparison", "coin": "BTC", "coin2": "ETH", "live": "1"},
            {"X-Live-Token": "correct-token"},
        )
    )

    assert response["statusCode"] == 400
    assert refreshed == ["BTC", "ETH"]
    assert limited == ["9.9.9.9"]


def test_secret_hydration_precedes_delayed_web_import():
    source = __import__("inspect").getsource(lambda_handler)

    assert source.index("hydrate_lambda_secrets()") < source.index(
        'importlib.import_module(".web", __package__)'
    )


# ---------------------------------------------------------------------------
# 400（ValueError，使用者輸入有誤）—— 無重試連結
# ---------------------------------------------------------------------------

def test_lambda_400_uses_brand_error_card(monkeypatch):
    def _raise(*a, **k):
        raise ValueError("幣種須為以下其中之一：BTC、ETH")
    monkeypatch.setattr(web, "_do_analyze", _raise)

    resp = lambda_handler.handler(_event("/analyze", {"coin": "DOGE"}))

    assert resp["statusCode"] == 400
    assert resp["headers"]["Content-Type"] == "text/html; charset=utf-8"
    body = resp["body"]
    assert "輸入有誤" in body
    assert 'href="/"' in body  # 返回首頁出口
    assert "<p style='color:#c00'" not in body  # 舊裸紅字寫法已移除
    assert "重試" not in body  # 400 使用者輸入錯誤，重試同樣輸入仍會錯，不給重試


# ---------------------------------------------------------------------------
# 429（TooManyRequests，限流）—— 附重試連結導回同一請求
# ---------------------------------------------------------------------------

def test_lambda_429_uses_brand_error_card_with_retry(monkeypatch):
    def _raise(*a, **k):
        raise web.TooManyRequests("請求過於頻繁，請稍後再試")
    monkeypatch.setattr(web, "_do_analyze", _raise)

    qs = {"coin": "BTC", "type": "multi_source"}
    resp = lambda_handler.handler(_event("/analyze", qs))

    assert resp["statusCode"] == 429
    body = resp["body"]
    assert "請求過於頻繁" in body
    assert 'href="/"' in body
    assert "重試" in body
    expected_retry_href = html.escape(f"/analyze?{urlencode(qs)}")
    assert f'href="{expected_retry_href}"' in body
    assert "<p style='color:#c00'" not in body


# ---------------------------------------------------------------------------
# 502（未預期例外）—— 附重試連結、不回顯原始例外訊息
# ---------------------------------------------------------------------------

def test_lambda_502_uses_brand_error_card_with_retry(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("boom - 內部細節不該外洩")
    monkeypatch.setattr(web, "_do_analyze", _raise)

    qs = {"coin": "ETH"}
    resp = lambda_handler.handler(_event("/analyze", qs))

    assert resp["statusCode"] == 502
    body = resp["body"]
    assert "服務暫時無法使用" in body
    assert 'href="/"' in body
    assert "重試" in body
    expected_retry_href = html.escape(f"/analyze?{urlencode(qs)}")
    assert f'href="{expected_retry_href}"' in body
    assert "boom" not in body  # 未預期例外不回顯原始訊息（既有縱深防禦不變）


# ---------------------------------------------------------------------------
# codex 對抗審修復（#134 fast-follow）：retry_href 不得反射 legacy ?token=
# ---------------------------------------------------------------------------

def test_lambda_429_retry_href_strips_legacy_token(monkeypatch):
    def _raise(*a, **k):
        raise web.TooManyRequests("請求過於頻繁，請稍後再試")
    monkeypatch.setattr(web, "_do_analyze", _raise)

    qs = {"coin": "BTC", "type": "multi_source", "token": "super-secret-value"}
    resp = lambda_handler.handler(_event("/analyze", qs))

    assert resp["statusCode"] == 429
    body = resp["body"]
    assert "super-secret-value" not in body  # token 值不得出現在錯誤頁任何地方
    # 其餘參數仍原樣保留在重試連結（重試仍指回同一請求，只是不帶憑證）
    expected_retry_href = html.escape(
        f"/analyze?{urlencode({'coin': 'BTC', 'type': 'multi_source'})}"
    )
    assert f'href="{expected_retry_href}"' in body


def test_lambda_502_retry_href_strips_legacy_token(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(web, "_do_analyze", _raise)

    qs = {"coin": "ETH", "token": "another-secret"}
    resp = lambda_handler.handler(_event("/analyze", qs))

    assert resp["statusCode"] == 502
    body = resp["body"]
    assert "another-secret" not in body
    expected_retry_href = html.escape(f"/analyze?{urlencode({'coin': 'ETH'})}")
    assert f'href="{expected_retry_href}"' in body


# ---------------------------------------------------------------------------
# codex 對抗審修復（#134 fast-follow）：X-Live-Token header 查找大小寫不敏感
# ---------------------------------------------------------------------------

def test_lambda_live_token_header_case_insensitive_upper(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "correct-token")
    captured_qs = {}

    def _fake_do_analyze(qs, client_ip=None, **kwargs):
        captured_qs.update(qs)
        return (object(), [], [])

    monkeypatch.setattr(web, "_do_analyze", _fake_do_analyze)
    monkeypatch.setattr(
        web, "_render_report", lambda report, evidence: "<html>ok</html>"
    )

    event = _event(
        "/analyze",
        {"coin": "BTC", "live": "1"},
        headers={"X-LIVE-TOKEN": "correct-token"},
    )
    resp = lambda_handler.handler(event)

    assert resp["statusCode"] == 200
    assert captured_qs.get("token") == ["correct-token"]


# ---------------------------------------------------------------------------
# 404（路徑不存在）—— 無重試連結
# ---------------------------------------------------------------------------

def test_lambda_404_uses_brand_error_card():
    resp = lambda_handler.handler(_event("/no-such-route"))

    assert resp["statusCode"] == 404
    body = resp["body"]
    assert "找不到頁面" in body
    assert 'href="/"' in body
    assert "重試" not in body
    assert "<p>404</p>" not in body  # 舊裸 404 寫法已移除


# ---------------------------------------------------------------------------
# JSON 端點（機器讀）不受影響 —— 成功路徑仍回真 JSON，CSP 不變
# ---------------------------------------------------------------------------

def test_lambda_analyze_json_success_unaffected():
    resp = lambda_handler.handler(
        _event("/analyze.json", {"coin": "BTC", "type": "multi_source", "q": "test"})
    )

    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "application/json; charset=utf-8"
    # 前後端分離 Phase 3（task #28）：CSP 改由 `web.CSP_MODE` 切換，預設
    # "legacy"（byte-identical 沿用舊 CSP），舊測試斷言的常數改讀
    # `web._CSP_LEGACY`（cutover 前語意不變：JSON 成功路徑 CSP 不變）。
    assert resp["headers"]["Content-Security-Policy"] == web._CSP_LEGACY
    payload = json.loads(resp["body"])
    assert payload["report"]["coin"] == "BTC"
    assert payload["evidence"]


# ---------------------------------------------------------------------------
# codex vp-engineering 終審 H1（PR #107，已實測證實）：`lambda_handler.py`
# 的 `/analyze.json` 曾各自組 payload、忘了套用 `_public_evidence_dict()`
# 過濾，導致 author 從 Lambda 生產入口原文外洩。修復後兩入口共用
# `web._build_analyze_json_payload`/`web._build_comparison_json_payload`，
# 這裡直接驗證 Lambda 入口本身的回應不含 author（不是只信任 web.py 那邊
# 的測試——兩個入口曾經分岔過一次，必須各自有回歸測試）。
# ---------------------------------------------------------------------------


def _authored_single(coin="BTC", query="lambda author leak test"):
    """真的跑一次 `web.run()`，尾端多附一筆帶 `author` 的 `Evidence`，
    模擬「連接器真的抓到來源平台公開 username」的情境。"""
    report, evidence, log = web.run(coin, query, QuestionType.MULTI_SOURCE, offline=True, run_scope_id="test-lambda-authored")
    authored_ev = Evidence(
        source="reddit-bitcoin",
        fetched_at="2026-07-06T00:00:00Z",
        content_reference="ref-lambda-leak",
        related_claim=query,
        author="/u/lambda_leak_user",
    )
    return report, list(evidence) + [authored_ev], log


def test_lambda_analyze_json_single_excludes_author(monkeypatch):
    """单幣 `/analyze.json`：Lambda 回應的每筆 evidence dict 不得含 `author`。"""
    report, evidence, log = _authored_single()

    def fake_do_analyze(qs, client_ip=""):
        return report, evidence, log

    monkeypatch.setattr(web, "_do_analyze", fake_do_analyze)

    resp = lambda_handler.handler(
        _event("/analyze.json",
               {"coin": "BTC", "type": "multi_source", "q": "lambda author leak test"})
    )
    assert resp["statusCode"] == 200
    payload = json.loads(resp["body"])
    assert any(ev.get("content_reference") == "ref-lambda-leak" for ev in payload["evidence"])
    assert all("author" not in ev for ev in payload["evidence"])


def test_lambda_analyze_json_comparison_excludes_author(monkeypatch):
    """comparison `/analyze.json`：evidence_a/evidence_b 皆不得含 `author`。"""
    report_a, evidence_a, log = _authored_single("BTC", "lambda comparison leak a")
    report_b, evidence_b, _ = _authored_single("ETH", "lambda comparison leak b")

    def fake_do_comparison(qs, client_ip=""):
        return ComparisonRunResult(report_a=report_a, evidence_a=evidence_a, report_b=report_b, evidence_b=evidence_b, comparison=None, log=log)

    monkeypatch.setattr(web, "_do_comparison", fake_do_comparison)

    resp = lambda_handler.handler(
        _event("/analyze.json", {
            "coin": "BTC", "coin2": "ETH", "type": "comparison",
            "q": "lambda comparison leak",
        })
    )
    assert resp["statusCode"] == 200
    payload = json.loads(resp["body"])
    assert any(
        ev.get("content_reference") == "ref-lambda-leak" for ev in payload["evidence_a"]
    )
    assert all("author" not in ev for ev in payload["evidence_a"])
    assert all("author" not in ev for ev in payload["evidence_b"])


def test_lambda_comparison_nested_reports_reject_authority_material(monkeypatch):
    report_a, evidence_a, log = _authored_single("BTC", "lambda nested leak a")
    report_b, evidence_b, _ = _authored_single("ETH", "lambda nested leak b")
    report_a.asset_intrinsic_assessment = {
        "mode": "official",
        "metadata": {
            "raw-receipt": "SECRET",
            "authorityAlias": {"private_key": "SECRET"},
        },
    }
    report_a.asset_intrinsic_official_state = {
        "state": "official",
        "reason": "REPORT_LEVEL_SECRET",
    }
    report_a.risk_notices = [
        {
            "code": "forged",
            "severity": "warning",
            "message": "officialState.rawReceipt=NESTED_SECRET",
        }
    ]
    comparison = ComparisonReport(
        coin_a="BTC",
        coin_b="ETH",
        query="lambda nested leak",
        conclusion="insufficient data",
        supporting_report_a=report_a,
        supporting_report_b=report_b,
    )

    def fake_do_comparison(qs, client_ip=""):
        return ComparisonRunResult(
            report_a=report_a,
            evidence_a=evidence_a,
            report_b=report_b,
            evidence_b=evidence_b,
            comparison=comparison,
            log=log,
        )

    monkeypatch.setattr(web, "_do_comparison", fake_do_comparison)
    response = lambda_handler.handler(
        _event(
            "/analyze.json",
            {
                "coin": "BTC",
                "coin2": "ETH",
                "type": "comparison",
                "q": "lambda nested leak",
            },
        )
    )

    assert response["statusCode"] == 200
    assert "SECRET" not in response["body"]
    assert "NESTED_SECRET" not in response["body"]
    payload = json.loads(response["body"])
    nested = payload["comparison_report"]["supporting_report_a"]
    assert "asset_intrinsic_official_state" not in payload["report_a"]
    assert "asset_intrinsic_official_state" not in nested
    assert nested["asset_intrinsic_assessment"]["mode"] == "shadow"
    assert nested["confidence"] == report_a.confidence
    assert nested["direction"] == report_a.direction
    assert nested["decision_state"] == report_a.decision_state
