"""AWS Lambda 進入點（Function URL）。

複用 web.py 的渲染與 pipeline，把 Lambda Function URL 事件轉成
與 web 服務一致的回應（HTML / JSON）。無第三方依賴（boto3 由 Lambda runtime 提供）。

部署：handler = trustforge.lambda_handler.handler
環境變數：TRUSTFORGE_HOME=/var/task；要真實 Bedrock 再設 BEDROCK_MODEL_ID + AWS_REGION。
live 模式須額外設 TRUSTFORGE_LIVE_TOKEN，且請求帶 `X-Live-Token` header
（query `?token=` fallback 已移除，只認 header）。
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from . import web
from .ingestion.hoyabit import log_hoyabit_startup_status


# Lambda has no conventional server ``main``; module initialization is its
# cold-start boundary.  This emits configuration status only and performs no
# network request.
log_hoyabit_startup_status()


def _resp(code, body, ctype):
    # 前後端分離 Phase 3（task #28）：CSP 切換旗標與 web.py 共用同一顆
    # `TRUSTFORGE_CSP_MODE`（預設 "legacy"，byte-identical 沿用既有
    # zero-JS 嚴格 CSP），"react" 才套用 harper 新指令集 + clickjacking/
    # referrer 防護，見 `web.py` CSP_MODE/`_CSP_LEGACY`/`_CSP_REACT` 說明。
    headers = {
        "Content-Type": ctype,
        "X-Content-Type-Options": "nosniff",
    }
    if web.CSP_MODE == "react":
        headers["Content-Security-Policy"] = web._CSP_REACT
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    else:
        headers["Content-Security-Policy"] = web._CSP_LEGACY
    return {
        "statusCode": code,
        "headers": headers,
        "body": body,
    }


def handler(event, context=None):
    # Function URL（payload v2）：rawPath + queryStringParameters(dict[str,str])
    path = (event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path", "/"))
    raw_qs = event.get("queryStringParameters") or {}
    qs = {k: [v] for k, v in raw_qs.items()}  # 轉成 _do_analyze 期望的 list 形式
    # CISO hardening R3（#2a, issue #134）：Function URL headers 是
    # dict[str, str]（一般已是小寫 key），包一層 `.get` 相容大小寫，跟 web.py
    # `Handler.do_GET` 共用同一個 `_apply_live_token_header`（只認
    # `X-Live-Token` header，query `token` 不再 fallback，只 log warning），
    # 維持兩個入口行為一致。
    _lambda_headers = event.get("headers") or {}
    # codex 對抗審 LOW（#134 fast-follow）：Function URL headers 雖然「一般」
    # 是小寫 key，但不是契約保證（不同 runtime/測試呼叫端仍可能帶大小寫混雜
    # 的 key，如 `X-LIVE-TOKEN`），先前只認 `x-live-token`/`X-Live-Token` 兩種
    # 固定拼法，其餘大小寫組合會被漏掉。這裡改成把所有 header key 正規化成
    # 小寫再查找，涵蓋任意大小寫拼法。
    _lambda_headers_lower = {
        (k or "").lower(): v for k, v in _lambda_headers.items()
    }
    # PR #99 終審 LOW：`raw_qs`（Lambda 原生 queryStringParameters dict）本來
    # 就會保留空值 key（`?token=`／裸 `?token` 都會是 `{"token": ""}`），直接用
    # `"token" in raw_qs` 判斷「query 是否帶了 token」，不看轉出來的 qs 值是否
    # truthy，避免空值 token 漏 warning。
    web._apply_live_token_header(
        qs,
        {"X-Live-Token": _lambda_headers_lower.get("x-live-token")},
        query_has_token="token" in raw_qs,
    )
    # issue #115（security）：請求入口凍結一次 config 快照（含 live
    # token / mode 判定），貫穿後續所有 live/real 判斷，消除同一次
    # 請求內「前半段判 live、後半段因 config 被外部改動判 real」的
    # 時序不一致。須在 `_apply_live_token_header` 收斂 token 之後、
    # 任何 live/real 分流之前呼叫。Lambda 單實例同時只處理一個事件，
    # 下個事件會再次覆寫快照，天然隔離（見 `web._begin_request_snapshot`
    # docstring）。
    web._begin_request_snapshot(qs)
    try:
        # 商業級一致性修（codex MEDIUM）：429/502 的「重試」連結要導回同一個請求
        # （比照 EC2 web.py 用 self.path），Lambda 沒有現成的 self.path，用
        # rawPath + 重組 query string 還原。
        #
        # codex 對抗審 MEDIUM（#134 fast-follow）：`raw_qs` 若帶了 legacy
        # `?token=secret`（即使該 fallback 已被 header-only 取代、下游不再採信
        # 這個 query 值），先前這裡原樣把它塞回 retry_href，一樣會把憑證性參數
        # 反射進 429/502 錯誤頁的重試連結（跟 web.py `self.path` 那三處同源問題）。
        # 這裡組 retry_href 前先濾掉 `token` key。
        _retry_qs = {k: v for k, v in raw_qs.items() if k != "token"}
        retry_href = path if not _retry_qs else f"{path}?{urlencode(_retry_qs)}"

        # 取 client IP（Lambda Function URL v2 requestContext）
        client_ip = (
            event.get("requestContext", {}).get("http", {}).get("sourceIp", "")
            or event.get("requestContext", {}).get("identity", {}).get("sourceIp", "")
            or ""
        )

        if path == "/healthz":
            return _resp(200, "ok", "text/plain; charset=utf-8")

        if path in ("/analyze", "/analyze.json"):
            # 提前解析 qtype 以便分流，不依賴回傳 tuple 長度
            from .schema import QuestionType
            qtype_raw = qs.get("type", ["multi_source"])[0]
            try:
                qtype = QuestionType(qtype_raw)
            except ValueError:
                qtype = QuestionType.MULTI_SOURCE

            try:
                if qtype == QuestionType.COMPARISON:
                    result = web._do_comparison(
                        qs, client_ip=client_ip
                    )
                    report_a, evidence_a, report_b, evidence_b, log = result
                    if path == "/analyze.json":
                        # codex vp-engineering 終審 H1（已實測證實 author 從此
                        # 入口外洩）：payload 組裝改呼叫 web.py 共用函式，跟
                        # web.py 自己的 `/analyze.json` 路由同一份，不再各自
                        # 複製、不會分岔（見 `web._build_comparison_json_payload`
                        # docstring）。
                        payload = web._build_comparison_json_payload(result)
                        return _resp(200, json.dumps(payload, ensure_ascii=False, indent=2),
                                     "application/json; charset=utf-8")
                    query = qs.get("q", [""])[0]
                    return _resp(
                        200,
                        web.render_page(
                            web._render_comparison(report_a, evidence_a, report_b, evidence_b, query)
                        ),
                        "text/html; charset=utf-8",
                    )
                else:
                    report, evidence, log = web._do_analyze(qs, client_ip=client_ip)
                    if path == "/analyze.json":
                        # 同上：呼叫 web.py 共用函式，不再自己組 payload。
                        payload = web._build_analyze_json_payload(report, evidence, log)
                        return _resp(200, json.dumps(payload, ensure_ascii=False, indent=2),
                                     "application/json; charset=utf-8")
                    return _resp(200, web.render_page(web._render_report(report, evidence)),
                                 "text/html; charset=utf-8")
            except web.TooManyRequests as exc:
                # 商業級一致性修（codex MEDIUM）：HTML 錯誤分支改用跟 EC2 web.py
                # 一致的品牌錯誤卡（`_render_error_card`），不再是裸紅字 `<p>`。
                # JSON 端點（machine-readable）不在此分支，格式不動。
                return _resp(429,
                             web.render_page(
                                 web._render_error_card(
                                     "請求過於頻繁", str(exc), retry_href=retry_href)),
                             "text/html; charset=utf-8")
            except ValueError as exc:
                return _resp(400,
                             web.render_page(
                                 web._render_error_card("輸入有誤", str(exc))),
                             "text/html; charset=utf-8")
            except Exception:
                logging.exception("TrustForge Lambda analyze error")
                return _resp(502,
                             web.render_page(
                                 web._render_error_card(
                                     "服務暫時無法使用", "分析服務暫時無法使用，請稍後再試",
                                     retry_href=retry_href)),
                             "text/html; charset=utf-8")

        if path == "/":
            return _resp(200, web.render_page(""), "text/html; charset=utf-8")
        return _resp(404,
                     web.render_page(
                         web._render_error_card(
                             "找不到頁面", "您造訪的網址不存在，請確認網址是否正確。")),
                     "text/html; charset=utf-8")

    finally:
        web._end_request_snapshot()
