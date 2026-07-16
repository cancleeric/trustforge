#!/usr/bin/env python3
"""Label JSON daily replay artifacts against later official OHLCV bars."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(REPO / "src"))
from trustforge.ingestion.prices import load_ohlcv, ohlcv_lineage
from trustforge.outcome_labeling import label_replay_outcomes
def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument("--coin", required=True); p.add_argument("--replay-dir", type=Path, required=True); p.add_argument("--data-dir", default=str(REPO / "data" / "data")); p.add_argument("--out", type=Path, required=True); a=p.parse_args()
 replays=[json.loads(f.read_text(encoding="utf-8")) for f in sorted(a.replay_dir.glob(f"{a.coin.lower()}-*.json"))]
 bars=load_ohlcv(a.coin,a.data_dir); result={"coin":a.coin,"labels":label_replay_outcomes(replays,bars,ohlcv_lineage(a.coin,a.data_dir,bars))}
 a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"coin":a.coin,"count":len(result["labels"])},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
