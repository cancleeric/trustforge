#!/usr/bin/env python3
from __future__ import annotations
import argparse
import signal
import time
import logging
from trustforge.analysis_flow import AnalysisFlow
from trustforge.hermes import autonomy_enabled

def main() -> int:
    p = argparse.ArgumentParser(description="Hermes continuous pre-analysis flow")
    p.add_argument("--coin", action="append", default=[])
    p.add_argument("--workers-per-stage", type=int, default=1)
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--poll-seconds", type=float, default=15.0)
    p.add_argument("--schedule-seconds", type=float, default=1800.0,
                   help="minimum interval between low-priority scheduled refreshes")
    args = p.parse_args()
    flow = AnalysisFlow(workers_per_stage=args.workers_per_stage)
    flow.start()
    if args.daemon:
        stopping = False
        last_prune = 0.0
        next_scheduled_refresh = 0.0
        def stop(*_args):
            nonlocal stopping; stopping = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        while not stopping:
            try:
                flow.reconcile_runtime()
                flow.adopt_pending()
                flow.adopt_due_retries()
                # The switch controls only low-priority scheduled snapshots.
                # Workers stay alive to serve durable manual jobs immediately.
                enabled, source = autonomy_enabled()
                now = time.monotonic()
                if enabled and now >= next_scheduled_refresh:
                    flow.refresh_once()
                    next_scheduled_refresh = now + max(60.0, args.schedule_seconds)
                else:
                    if not enabled:
                        # Re-enabling starts one fresh scheduled cycle without
                        # delaying manual adoption or retaining stale timing.
                        next_scheduled_refresh = 0.0
                        logging.debug("Hermes scheduled refresh paused (%s); manual queue remains active", source)
                if time.time() - last_prune >= 86400:
                    flow.prune(); last_prune = time.time()
            except Exception:
                logging.exception("Hermes daemon iteration failed; retrying next interval")
            deadline = time.monotonic() + max(0.5, args.poll_seconds)
            while not stopping and time.monotonic() < deadline: time.sleep(.25)
        flow.join(); flow.stop()
    else:
        enabled, source = autonomy_enabled()
        if not enabled:
            print(f"[run_analysis_flow] scheduled analysis disabled ({source}); no batch refresh started")
            flow.stop()
            return 0
        for coin in args.coin or ["BTC", "ETH", "SOL", "BNB", "XRP"]:
            flow.enqueue_matrix(flow.create_snapshot(coin))
        flow.join(); flow.stop()
    return 0

if __name__ == "__main__": raise SystemExit(main())
