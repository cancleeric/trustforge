"""TrustForge demo / 競賽執行入口。

產出官方 4 交付件到 --out 目錄：
  report.md  evidence.json  execution_log.jsonl  （程式碼/設定即本 repo）

    python -m trustforge.cli analyze --coin BTC \
        --query "分析 BTC 過去兩週市場狀況" --type multi_source --offline
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .agent.orchestrator import build_report
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import collect
from .schema import COIN_POOL, QuestionType
from .trust.scoring import aggregate, extract_claims, score


def cmd_analyze(args: argparse.Namespace) -> int:
    coin = args.coin.upper()
    if coin not in COIN_POOL:
        print(f"幣種須為 {COIN_POOL} 之一")
        return 2
    qtype = QuestionType(args.type)

    log = ExecutionLog()
    log.record("ingestion.collect", params={"coin": coin, "offline": args.offline})
    docs = collect(args.query, coin=coin, offline=args.offline, data_dir=args.data_dir)
    if not docs:
        print("（無資料：offline 請確認 demo/sample_data 與 ohlcv/，線上請接連接器）")
        return 1

    if args.fixed_now:
        now = float(args.fixed_now)
    elif args.offline:
        now = max((d.ts for d in docs), default=time.time())
    else:
        now = time.time()

    claims = extract_claims(docs)
    scored = score(claims, now=now)
    log.record("trust.score", summary=f"claims={len(claims)}")
    brief = aggregate(scored, args.query)
    log.record("trust.aggregate", summary=f"supporting={len(brief.supporting)} confidence={brief.confidence:.2f}")

    report, evidence = build_report(
        args.query, coin, qtype, brief,
        client=BedrockClient(offline=args.offline), log=log,
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report.to_markdown(evidence), encoding="utf-8")
    (out / "evidence.json").write_text(
        json.dumps([e.to_dict() for e in evidence], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "execution_log.jsonl").write_text(log.to_jsonl(), encoding="utf-8")

    print(f"完成。耗時 {log.elapsed():.2f}s（上限 900s），交付件寫入 {out}/")
    print(f"  report.md / evidence.json ({len(evidence)} 筆) / execution_log.jsonl")
    if not args.quiet:
        print("\n" + report.to_markdown(evidence))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trustforge", description="多源資訊信任提煉的加密市場分析 agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="對指定幣種與題目做信任提煉分析")
    a.add_argument("--coin", required=True, help=f"{COIN_POOL}")
    a.add_argument("--query", required=True, help="題目 / 問題")
    a.add_argument("--type", default="multi_source",
                   choices=[t.value for t in QuestionType], help="題型")
    a.add_argument("--offline", action="store_true", help="用離線樣本資料，不需 AWS")
    a.add_argument("--data-dir", default=None, help="OHLCV CSV 目錄（預設離線樣本）")
    a.add_argument("--out", default="out", help="交付件輸出目錄")
    a.add_argument("--fixed-now", default=None, help="固定 now epoch（測試用）")
    a.add_argument("--quiet", action="store_true", help="不在 stdout 印報告")
    a.set_defaults(func=cmd_analyze)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
