"""TrustForge demo / 競賽執行入口。

產出官方 4 交付件到 --out 目錄：
  report.md  evidence.json  execution_log.jsonl  （程式碼/設定即本 repo）

    python -m trustforge.cli analyze --coin BTC \
        --query "分析 BTC 過去兩週市場狀況" --type multi_source --offline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import run
from .schema import COIN_POOL, QuestionType


def cmd_analyze(args: argparse.Namespace) -> int:
    coin = args.coin.upper()
    if coin not in COIN_POOL:
        print(f"幣種須為 {COIN_POOL} 之一")
        return 2
    qtype = QuestionType(args.type)

    try:
        report, evidence, log = run(coin, args.query, qtype,
                                    offline=args.offline, data_dir=args.data_dir)
    except ValueError as e:
        print(f"（{e}）")
        return 1

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
    a.add_argument("--quiet", action="store_true", help="不在 stdout 印報告")
    a.set_defaults(func=cmd_analyze)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
