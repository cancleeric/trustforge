"""TrustForge demo / 競賽執行入口。

產出官方 4 交付件到 --out 目錄：
  report.md  evidence.json  execution_log.jsonl  （程式碼/設定即本 repo）

用法：
  python -m trustforge.cli analyze --coin BTC \\
      --query "分析 BTC 過去兩週市場狀況" --type multi_source --offline

  python -m trustforge.cli analyze --coin BTC,ETH \\
      --query "比較 BTC 與 ETH 當前市場位置" --type comparison --offline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import run, run_comparison
from .runtime_control import runtime_control, set_runtime_enabled
from .schema import COIN_POOL, QuestionType, comparison_to_markdown


def cmd_analyze(args: argparse.Namespace) -> int:
    qtype = QuestionType(args.type)

    # ── comparison：需要兩個幣種 ────────────────────────────────
    if qtype == QuestionType.COMPARISON:
        raw = (args.coin or "").strip()
        parts = [c.strip().upper() for c in raw.split(",") if c.strip()]
        if len(parts) != 2:
            print(
                "comparison 題型需提供兩個幣種，格式：--coin BTC,ETH\n"
                f"（目前收到：{raw!r}）"
            )
            return 2
        coin_a, coin_b = parts
        try:
            report_a, evidence_a, report_b, evidence_b, log = run_comparison(
                coin_a, coin_b, args.query,
                offline=args.offline, data_dir=args.data_dir,
            )
        except ValueError as e:
            print(f"錯誤（{e}）")
            return 1

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)

        md = comparison_to_markdown(report_a, evidence_a, report_b, evidence_b, args.query)
        (out / "report.md").write_text(md, encoding="utf-8")

        # evidence.json：兩幣合併，每筆加 coin 欄位標明歸屬
        all_ev = (
            [{**e.to_dict(), "coin": report_a.coin} for e in evidence_a]
            + [{**e.to_dict(), "coin": report_b.coin} for e in evidence_b]
        )
        (out / "evidence.json").write_text(
            json.dumps(all_ev, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "execution_log.jsonl").write_text(log.to_jsonl(), encoding="utf-8")

        total = len(evidence_a) + len(evidence_b)
        print(f"完成。耗時 {log.elapsed():.2f}s（上限 900s），交付件寫入 {out}/")
        print(
            f" report.md / evidence.json ({total} 筆，{coin_a}+{coin_b})"
            " / execution_log.jsonl"
        )
        if not args.quiet:
            print("\n" + md)
        return 0

    # ── 單一幣種（multi_source / hypothesis）────────────────────
    coin = (args.coin or "").upper()
    if coin not in COIN_POOL:
        print(f"幣種須為 {COIN_POOL} 之一")
        return 2

    try:
        report, evidence, log = run(coin, args.query, qtype,
                                    offline=args.offline, data_dir=args.data_dir)
    except ValueError as e:
        print(f"錯誤（{e}）")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report.to_markdown(evidence), encoding="utf-8")
    (out / "evidence.json").write_text(
        json.dumps([e.to_dict() for e in evidence], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "execution_log.jsonl").write_text(log.to_jsonl(), encoding="utf-8")

    print(f"完成。耗時 {log.elapsed():.2f}s（上限 900s），交付件寫入 {out}/")
    print(f" report.md / evidence.json ({len(evidence)} 筆) / execution_log.jsonl")
    if not args.quiet:
        print("\n" + report.to_markdown(evidence))
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    if args.action in {"start", "stop"}:
        control = set_runtime_enabled(
            args.action == "start",
            reason=args.reason or f"trustforge control {args.action}",
            actor="cli",
        )
    else:
        control = runtime_control()
    status = "enabled" if control.enabled else "disabled"
    print(
        f"runtime {status} "
        f"(source={control.source}, production={control.production}, "
        f"production_continuous_allowed={control.production_continuous_allowed})"
    )
    print(f"state={control.state_path}")
    if control.reason:
        print(f"reason={control.reason}")
    if args.json:
        print(json.dumps(control.__dict__, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="trustforge",
        description="多源資訊信任提煉的加密市場分析 Agent",
    )
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("analyze", help="分析指定幣種")
    a.add_argument(
        "--coin",
        help=f"幣種（{COIN_POOL}）；comparison 題型請用逗號分隔兩個幣種，例如 BTC,ETH",
    )
    a.add_argument("--query", default="分析該幣種近兩週市場狀況，整合多源資料")
    a.add_argument(
        "--type",
        default="multi_source",
        choices=[t.value for t in QuestionType],
        dest="type",
    )
    a.add_argument("--offline", action="store_true", help="離線模式（不呼叫 AWS Bedrock）")
    a.add_argument("--data-dir", dest="data_dir", default=None, help="OHLCV CSV 目錄")
    a.add_argument("--out", default="out", help="輸出目錄（預設 out/）")
    a.add_argument("--quiet", action="store_true", help="不印出完整報告")
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("control", help="啟動/停止本機 runtime switch；production continuous work 預設關閉")
    c.add_argument("action", choices=["start", "stop", "status"])
    c.add_argument("--reason", default="", help="寫入 runtime switch 的原因")
    c.add_argument("--json", action="store_true", help="輸出 JSON 狀態")
    c.set_defaults(func=cmd_control)

    args = p.parse_args(argv)
    if not hasattr(args, "func"):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
