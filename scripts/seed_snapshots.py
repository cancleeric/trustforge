#!/usr/bin/env python3
"""一次性跑 5 幣快照填入 analysis_flow DB，讓前端有資料顯示。"""
from trustforge.analysis_flow import AnalysisFlow

flow = AnalysisFlow()
flow.start()
for coin in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
    sid = flow.create_snapshot(coin)
    flow.enqueue_matrix(sid)
    print(f"{coin}: snapshot={sid}")
flow.join()
flow.stop()
print("Done - all snapshots processed")
