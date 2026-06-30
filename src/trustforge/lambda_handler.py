"""AWS Lambda 進入點（Function URL）。

複用 web.py 的渲染與 pipeline，把 Lambda Function URL 事件轉成
與 web 服務一致的回應（HTML / JSON）。無第三方依賴（boto3 由 Lambda runtime 提供）。

部署：handler = trustforge.lambda_handler.handler
環境變數：TRUSTFORGE_HOME=/var/task；要真實 Bedrock 再設 BEDROCK_MODEL_ID + AWS_REGION。
"""
from __future__ import annotations

import dataclasses
import json

from . import web


def _resp(code, body, ctype):
    return {"statusCode": code,
            "headers": {"Content-Type": ctype},
            "body": body}


def handler(event, context=None):
    # Function URL（payload v2）：rawPath + queryStringParameters(dict[str,str])
    path = (event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path", "/"))
    raw_qs = event.get("queryStringParameters") or {}
    qs = {k: [v] for k, v in raw_qs.items()}  # 轉成 web._do_analyze 期望的 list 形式

    if path == "/healthz":
        return _resp(200, "ok", "text/plain; charset=utf-8")

    if path in ("/analyze", "/analyze.json"):
        try:
            report, evidence, log = web._do_analyze(qs)
        except ValueError as e:
            return _resp(400, web.render_page(f"<p style='color:#c00'>{e}</p>"),
                         "text/html; charset=utf-8")
        if path == "/analyze.json":
            payload = {
                "report": dataclasses.asdict(report),
                "evidence": [ev.to_dict() for ev in evidence],
                "execution_log": log.events,
            }
            return _resp(200, json.dumps(payload, ensure_ascii=False, indent=2),
                         "application/json; charset=utf-8")
        return _resp(200, web.render_page(web._render_report(report, evidence)),
                     "text/html; charset=utf-8")

    if path == "/":
        return _resp(200, web.render_page(""), "text/html; charset=utf-8")
    return _resp(404, web.render_page("<p>404</p>"), "text/html; charset=utf-8")
