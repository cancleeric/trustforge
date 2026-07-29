#!/usr/bin/env python3
"""Submit one real manual analysis and wait for its published report."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
import uuid


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
) -> dict:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
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
    question = (
        "Production release canary "
        f"{uuid.uuid4().hex}: 評估 BTC 整體信任狀態與操縱風險。"
    )
    receipt = _request_json(
        f"{base_url.rstrip('/')}/api/analysis-question",
        method="POST",
        payload={
            "coin": "BTC",
            "mode": "risk",
            "question": question,
            "locale": "zh-Hant",
        },
    )
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
