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

from trustforge import lambda_handler, web


def _event(path: str, qs: dict | None = None) -> dict:
    """組一個 Lambda Function URL（payload v2）事件。"""
    event = {
        "rawPath": path,
        "requestContext": {"http": {"sourceIp": "9.9.9.9"}},
    }
    if qs is not None:
        event["queryStringParameters"] = qs
    return event


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
    assert resp["headers"]["Content-Security-Policy"] == lambda_handler._CSP
    payload = json.loads(resp["body"])
    assert payload["report"]["coin"] == "BTC"
    assert payload["evidence"]
