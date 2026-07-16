#!/usr/bin/env python3
from __future__ import annotations
import argparse
import signal
import time
import logging
from trustforge.analysis_flow import AnalysisFlow

def main() -> int:
    p = argparse.ArgumentParser(description="Hermes continuous pre-analysis flow")
    p.add_argument("--coin", action="append", default=[])
    p.add_argument("--workers-per-stage", type=int, default=1)
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--poll-seconds", type=float, default=15.0)
    args = p.parse_args()
    flow = AnalysisFlow(workers_per_stage=args.workers_per_stage)
    flow.start()
    if args.daemon:
        stopping = False
        last_prune = 0.0
        def stop(*_args):
            nonlocal stopping; stopping = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        while not stopping:
            try:
                flow.adopt_pending()
                flow.adopt_due_retries()
                flow.refresh_once()
                if time.time() - last_prune >= 86400:
                    flow.prune(); last_prune = time.time()
            except Exception:
                logging.exception("Hermes daemon iteration failed; retrying next interval")
            deadline = time.monotonic() + max(1.0, args.poll_seconds)
            while not stopping and time.monotonic() < deadline: time.sleep(.25)
        flow.join(); flow.stop()
    else:
        for coin in args.coin or ["BTC", "ETH", "SOL", "BNB", "XRP"]:
            flow.enqueue_matrix(flow.create_snapshot(coin))
        flow.join(); flow.stop()
    return 0

if __name__ == "__main__": raise SystemExit(main())
