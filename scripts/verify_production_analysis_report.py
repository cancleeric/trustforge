#!/usr/bin/env python3
"""Submit one real manual analysis and wait for its published report."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    idempotency_key: str | None = None,
    cookie: str | None = None,
) -> dict:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if cookie is not None:
        headers["Cookie"] = cookie
    request = urllib.request.Request(
        url, data=body, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        document = json.load(response)
    if not document.get("ok") or not isinstance(document.get("data"), dict):
        raise RuntimeError(f"analysis canary API rejected request: {document}")
    return document["data"]


def verify_report(
    base_url: str,
    *,
    timeout_seconds: int = 600,
    poll_seconds: int = 5,
) -> dict:
    """Two-phase formal submit then poll for the published report.

    Phase 1: POST /api/analysis-question without a scope cookie → server issues
    a 428 with Set-Cookie (__Host-tf_formal_scope / tf_formal_scope).
    Phase 2: replay the same Idempotency-Key + payload with the cookie → submit
    accepted, returns job_id. Then poll /api/analysis-job until completed.
    """
    question = (
        "Production release canary "
        f"{secrets.token_hex(16)}: 評估 BTC 整體信任狀態與操縱風險。"
    )
    payload: dict[str, object] = {
        "coin": "BTC",
        "mode": "risk",
        "question": question,
        "locale": "zh-Hant",
        "fresh": True,
    }
    epoch = datetime.now(timezone.utc).strftime("%Y%m")
    random_part = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
    idempotency_key = f"tf1.{epoch}.{random_part}"
    submit_url = f"{base_url.rstrip('/')}/api/analysis-question"

    cookie = None
    receipt = None
    for attempt in range(2):
        try:
            receipt = _request_json(
                submit_url,
                method="POST",
                payload=payload,
                idempotency_key=idempotency_key,
                cookie=cookie,
            )
            break  # phase 2 succeeded (or phase 1 already had a cookie)
        except urllib.error.HTTPError as exc:
            if exc.code == 428 and attempt == 0:
                set_cookie = exc.headers.get("Set-Cookie")
                if not set_cookie:
                    raise RuntimeError(
                        "formal canary: 428 without Set-Cookie (scope cookie)"
                    ) from exc
                cookie = set_cookie.split(";", 1)[0].strip()  # name=value
                continue
            raise
    if receipt is None:
        raise RuntimeError("formal canary: submit did not complete")

    job_id = receipt.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"analysis canary did not return a job: {receipt}")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        query = urllib.parse.urlencode({"job_id": job_id})
        job = _request_json(
            f"{base_url.rstrip('/')}/api/analysis-job?{query}"
        )
        state = job.get("state")
        if state == "completed":
            if not isinstance(job.get("result"), dict):
                raise RuntimeError(
                    "analysis canary completed without a report payload"
                )
            return {
                "job_id": job_id,
                "state": state,
                "current_stage": job.get("current_stage"),
            }
        if state == "failed":
            raise RuntimeError(
                f"analysis canary failed: {job.get('error') or 'unknown error'}"
            )
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"analysis canary did not produce a report within {timeout_seconds}s"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    result = verify_report(
        args.base_url,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
