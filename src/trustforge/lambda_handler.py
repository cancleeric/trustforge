"""AWS Lambda 進入點（Function URL）。

複用 web.py 的渲染與 pipeline，把 Lambda Function URL 事件轉成
與 web 服務一致的回應（HTML / JSON）。無第三方依賴（boto3 由 Lambda runtime 提供）。

部署：handler = trustforge.lambda_handler.handler
環境變數：TRUSTFORGE_HOME=/var/task；要真實 Bedrock 再設 BEDROCK_MODEL_ID + AWS_REGION。
live 模式須額外設 TRUSTFORGE_LIVE_TOKEN，且請求帶對應 token 參數。
"""
from __future__ import annotations

import dataclasses
import json
import logging
from urllib.parse import urlencode

from . import web

_CSP = "default-src 'none'; style-src 'unsafe-inline'"


def _resp(code, body, ctype):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": ctype,
            "Content-Security-Policy": _CSP,
            "X-Content-Type-Options": "nosniff",
        },
        "body": body,
    }


def handler(event, context=None):
    # Function URL（payload v2）：rawPath + queryStringParameters(dict[str,str])
    path = (event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path", "/"))
    raw_qs = event.get("queryStringParameters") or {}
    qs = {k: [v] for k, v in raw_qs.items()}  # 轉成 _do_analyze 期望的 list 形式
    # 商業級一致性修（codex MEDIUM）：429/502 的「重試」連結要導回同一個請求
    # （比照 EC2 web.py 用 self.path），Lambda 沒有現成的 self.path，用
    # rawPath + 重組 query string 還原。
    retry_href = path if not raw_qs else f"{path}?{urlencode(raw_qs)}"

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
                report_a, evidence_a, report_b, evidence_b, log = web._do_comparison(
                    qs, client_ip=client_ip
                )
                if path == "/analyze.json":
                    payload = {
                        "version": web.VERSION,
                        "report_a": dataclasses.asdict(report_a),
                        "evidence_a": [ev.to_dict() for ev in evidence_a],
                        "report_b": dataclasses.asdict(report_b),
                        "evidence_b": [ev.to_dict() for ev in evidence_b],
                        "execution_log": log.events,
                    }
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
                    payload = {
                        "version": web.VERSION,
                        "report": dataclasses.asdict(report),
                        "evidence": [ev.to_dict() for ev in evidence],
                        "execution_log": log.events,
                    }
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
