"""TrustForge demo 入口。

    python -m trustforge.cli analyze --query "BTC 今天為什麼急跌？" --offline
"""
from __future__ import annotations

import argparse
import json
import time

from .agent.orchestrator import analyze
from .bedrock import BedrockClient
from .ingestion.base import collect
from .trust.scoring import aggregate, extract_claims, score


def cmd_analyze(args: argparse.Namespace) -> int:
    docs = collect(args.query, offline=args.offline)
    if not docs:
        print("（無資料：offline 模式請確認 demo/sample_data，線上模式請接連接器）")
        return 1
    if args.fixed_now:
        now = float(args.fixed_now)
    elif args.offline:
        # 離線樣本視為「最新」資訊：以最新一筆為基準，時效衰減才有意義
        now = max((d.ts for d in docs), default=time.time())
    else:
        now = time.time()
    claims = extract_claims(docs)
    scored = score(claims, now=now)
    brief = aggregate(scored, args.query)
    result = analyze(brief, client=BedrockClient(offline=args.offline))

    print(f"\n=== TrustForge 分析：{result.query} ===")
    print(f"整體信心：{result.confidence:.2f}\n")
    print("【分析】")
    print(result.narrative)
    print("\n【溯源鏈 provenance】")
    print(json.dumps(result.provenance, ensure_ascii=False, indent=2))
    if result.contrarian:
        print("\n【反方 / 低信任證據】")
        for c in result.contrarian:
            print(f"  - {c}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trustforge", description="多源資訊信任提煉的加密市場分析 agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="對一個查詢做信任提煉分析")
    a.add_argument("--query", required=True)
    a.add_argument("--offline", action="store_true", help="用離線樣本資料，不需 AWS")
    a.add_argument("--fixed-now", default=None, help="固定 now epoch 秒（測試用）")
    a.set_defaults(func=cmd_analyze)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
