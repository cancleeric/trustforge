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
import os
import tempfile
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


def cmd_smoke(args: argparse.Namespace) -> int:
    """Bedrock smoke test：驗證 AWS Bedrock 連線可用（issue #202）。"""
    # 直接使用 smoke 模組的邏輯（避免依賴 scripts/ 路徑）
    from .smoke import run_smoke
    return run_smoke(out_dir=args.out)


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


def cmd_backfill(args: argparse.Namespace) -> int:
    from .backfill import (
        BackfillWorker, backfill_enabled, set_backfill_enabled,
    )

    coins = (
        [c.strip().upper() for c in args.coin.split(",") if c.strip()]
        if args.coin else None
    )

    if args.action == "plan":
        worker = BackfillWorker(
            coins=coins, start_date=args.start, end_date=args.end,
            data_dir=args.data_dir, mode=args.mode, sample=args.sample,
        )
        plan = worker.plan()
        total = sum(plan.values())
        print(f"回填計畫：共 {total} 天")
        for coin, days in plan.items():
            print(f"  {coin}: {days} 天")
        worker.close()
        return 0

    if args.action == "status":
        worker = BackfillWorker(
            coins=coins, start_date=args.start, end_date=args.end,
            data_dir=args.data_dir, mode=args.mode, sample=args.sample,
        )
        status = worker.status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            ctrl = backfill_enabled()
            state = "啟用" if ctrl.enabled else "停用"
            print(f"回填系統：{state}（source={ctrl.source}）")
            print(f"進度：{status['total_completed']}/{status['total_days']}"
                  f"（{status['progress_pct']}%）")
            print(f"剩餘：{status['total_remaining']} 天")
            for coin, p in status["per_coin"].items():
                last = p.get("last_completed_date") or "—"
                print(f"  {coin}: {p['completed_days']}/{p['total_days']}"
                      f"  最後={last}  狀態={p['state']}")
        worker.close()
        return 0

    if args.action == "stop":
        set_backfill_enabled(False, reason="cli stop", actor="cli")
        print("回填已停止（state file 已寫入 enabled=false）")
        return 0

    if args.action == "reset-failed":
        worker = BackfillWorker(
            coins=coins, start_date=args.start, end_date=args.end,
            data_dir=args.data_dir, mode=args.mode, sample=args.sample,
        )
        count = worker.reset_failed()
        print(f"已重設 {count} 個失敗任務為 pending")
        worker.close()
        return 0

    # action == "start"
    set_backfill_enabled(True, reason="cli start", actor="cli")
    worker = BackfillWorker(
        coins=coins, start_date=args.start, end_date=args.end,
        batch_size=args.batch_size, interval_sec=args.interval,
        data_dir=args.data_dir, mode=args.mode, sample=args.sample,
    )
    seeded = worker.seed_tasks()
    plan = worker.plan()
    total = sum(plan.values())
    print(f"回填啟動：{total} 天目標，新增 {seeded} 個任務")

    if args.daemon:
        import signal
        print("前台持續執行中（Ctrl+C 停止）...")
        stopping = False

        def _stop(*_a: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        worker.start_daemon()
        while not stopping and worker.is_running:
            import time as _time
            _time.sleep(1)
        worker.close()
        print("回填已結束")
    else:
        # 跑一個 batch 然後退出
        results = worker.run_batch()
        completed = sum(1 for r in results if r.state == "completed")
        failed = sum(1 for r in results if r.state == "failed")
        print(f"本批完成：{completed} 成功, {failed} 失敗, {len(results)} 處理")
        status = worker.status()
        print(f"總進度：{status['total_completed']}/{status['total_days']}"
              f"（{status['progress_pct']}%）")
        worker.close()
    return 0


def cmd_train_calibration(args: argparse.Namespace) -> int:
    """從 training data 訓練 isotonic calibration model（issue #343）。

    邏輯：
    1. 讀取 training-data 中有方向預測的記錄
    2. 與 OHLCV T+7 實際價格比對，算出 hit/miss
    3. 訓練 isotonic model
    4. 存入 model-artifacts
    """
    import csv
    from datetime import timedelta

    from .calibration_model import save_calibration_model, train_isotonic

    training_dir = Path(args.training_dir)
    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    if not training_dir.exists():
        print(f"Training data 目錄不存在：{training_dir}")
        return 1

    # 讀取所有幣種的 OHLCV（date → close price）
    ohlcv_map: dict[str, dict[str, float]] = {}  # coin → {date_str → close}
    for csv_file in data_dir.glob("*_daily_ohlcv.csv"):
        coin = csv_file.stem.replace("_daily_ohlcv", "").upper()
        prices: dict[str, float] = {}
        with open(csv_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                prices[row["date"]] = float(row["close"])
        ohlcv_map[coin] = prices

    if not ohlcv_map:
        print(f"OHLCV 目錄無資料：{data_dir}")
        return 1

    # 讀取 training data，收集 (confidence, hit_flag) 對
    confidences: list[float] = []
    hit_flags: list[bool] = []
    skipped = 0
    total = 0

    for jsonl_file in training_dir.glob("*.jsonl"):
        coin = jsonl_file.stem.upper()
        if coin not in ohlcv_map:
            continue
        prices = ohlcv_map[coin]

        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue

                total += 1
                direction = rec.get("direction", "")
                confidence = rec.get("confidence")
                date_str = rec.get("date")

                if confidence is None or date_str is None:
                    skipped += 1
                    continue

                # 計算 T+7 日期
                from datetime import date as _date
                try:
                    pred_date = _date.fromisoformat(date_str)
                except ValueError:
                    skipped += 1
                    continue

                t7_date = pred_date + timedelta(days=7)
                t7_str = t7_date.isoformat()
                t0_str = date_str

                if t0_str not in prices or t7_str not in prices:
                    skipped += 1
                    continue

                # 計算 T+7 相對 T0 的變化
                p0 = prices[t0_str]
                p7 = prices[t7_str]
                if p0 == 0:
                    skipped += 1
                    continue
                change_pct = (p7 - p0) / p0

                # Hit 判定（calibration_runner 邏輯）：
                # - 中性/不明 + |change| < 2% → hit
                # - 偏多 + change > 0 → hit
                # - 偏空 + change < 0 → hit
                # - 其他 → miss
                hit = _judge_hit(direction, change_pct)

                confidences.append(float(confidence))
                hit_flags.append(hit)

    eligible = len(confidences)
    print(f"共讀取 {total} 筆記錄，{skipped} 筆跳過，{eligible} 筆可用")

    if eligible < 5:
        print("可用樣本不足（< 5），無法訓練模型")
        return 1

    # 訓練
    points = train_isotonic(confidences, hit_flags)
    save_calibration_model(points, out_path, sample_count=eligible)
    print(f"模型已存入：{out_path}（{len(points)} 個校準點，{eligible} 筆樣本）")
    return 0


def _judge_hit(direction: str, change_pct: float) -> bool:
    """Hit 判定邏輯（對齊 calibration_runner spec）。

    - 中性 / 不明：|change| < 2% → hit
    - 偏多：change > 0 → hit
    - 偏空：change < 0 → hit
    - 其他方向未知：視為中性
    """
    from .calibration_metrics import judge_direction_hit

    return judge_direction_hit(direction, change_pct)


def cmd_label_outcomes(args: argparse.Namespace) -> int:
    """用 OHLCV T+N outcome 標記 ground truth 方向（issue #378）。"""
    from .outcome_labeler import label_outcomes
    stats = label_outcomes(
        Path(args.training_dir),
        Path(args.data_dir),
        horizon=args.horizon,
        threshold=args.threshold / 100,
    )
    for coin, s in stats.items():
        print(f"{coin}: {s['labeled']}/{s['total']} labeled")
    return 0


def cmd_security_gate(args: argparse.Namespace) -> int:
    """投稿前安全掃描（issue #205）。"""
    from .security_gate import run_security_gate
    return run_security_gate(out_dir=args.out)


def cmd_qa_matrix(args: argparse.Namespace) -> int:
    """QA mini matrix：5 幣 × 3 題型退化檢查（issue #203）。"""
    from .qa_matrix import main as qa_main
    return qa_main(offline=args.offline, data_dir=args.data_dir, out_dir=args.out)


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Historical replay calibration diagnostic from JSONL training archives."""
    from .calibration import load_training_snapshots, replay_report
    from .ingestion.prices import load_ohlcv
    from .improvement import diagnose

    snapshots = load_training_snapshots(args.training_data, coin=args.coin)
    report = replay_report(args.coin, snapshots, load_ohlcv(args.coin, args.data_dir))
    diagnostic = diagnose(replay=report)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report, "diagnostic": diagnostic}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"calibrate 完成：{args.coin.upper()} eligible="
        f"{report['horizons']['T+1']['eligible_predictions']} "
        f"calibration_error={report['horizons']['T+1']['calibration_error']} -> {out}"
    )
    return 0


def cmd_modelhub_train(args: argparse.Namespace) -> int:
    """Run isolated ModelHub calibrator proposal flows and print JSON summaries."""
    from .modelhub_submit import submit_calibrator_training
    from .execlog import ExecutionLog

    coins = list(COIN_POOL) if args.all else [args.coin]
    request_map: dict[str, str] = {}
    for item in args.req_no_map:
        if "=" not in item:
            print(json.dumps({"status": "error", "coin": ""}))
            return 1
        coin_key, request_number = item.split("=", 1)
        if coin_key in request_map or coin_key not in COIN_POOL or not request_number:
            print(json.dumps({"status": "error", "coin": coin_key}))
            return 1
        request_map[coin_key] = request_number
    if not args.all and request_map:
        print(json.dumps({"status": "error", "coin": args.coin}))
        return 1
    if args.all and not args.dry_run:
        if set(request_map) != set(COIN_POOL) or len(set(request_map.values())) != len(COIN_POOL):
            print(json.dumps({"status": "error", "coin": ""}))
            return 1
    results = []
    for coin in coins:
        log = ExecutionLog()
        try:
            result = submit_calibrator_training(
                coin,
                training_dir=Path(args.training_dir),
                out_dir=Path(args.out_dir),
                req_no=request_map[coin] if args.all and not args.dry_run else args.req_no,
                dry_run=args.dry_run,
                execution_log=log,
            )
        except Exception as exc:
            log.record(
                "modelhub.training.error", {"coin": coin, "exception_type": type(exc).__name__},
                "Unexpected ModelHub orchestration error",
            )
            result = {"status": "error", "coin": coin, "run_id": log.run_id}
        if (
            not isinstance(result, dict)
            or result.get("status") not in {
                "blocked", "unavailable", "timeout", "no_improvement", "error", "candidate", "dry_run",
            }
            or result.get("coin") != coin
        ):
            log.record(
                "modelhub.training.error", {"coin": coin, "exception_type": "InvalidResult"},
                "Malformed ModelHub orchestration result",
            )
            result = {"status": "error", "coin": coin, "run_id": log.run_id}
        result["run_id"] = log.run_id
        log_dir = Path(args.out_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".execution-", suffix=".tmp", dir=log_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(log.to_jsonl())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, log_dir / f"execution-{log.run_id}.jsonl")
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            result = {"status": "error", "coin": coin, "run_id": log.run_id}
        results.append(result)
    print(json.dumps(results if args.all else results[0], ensure_ascii=False, sort_keys=True))
    statuses = {result["status"] for result in results}
    if statuses & {"unavailable", "timeout", "error"}:
        return 1
    if statuses & {"blocked", "no_improvement"}:
        return 2
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

    s = sub.add_parser("smoke", help="Bedrock smoke test：驗證 AWS Bedrock 連線可用")
    s.add_argument("--out", default="out", help="Artifact 輸出目錄（預設 out/）")
    s.set_defaults(func=cmd_smoke)

    bf = sub.add_parser("backfill", help="歷史回填系統：用 5 年 OHLCV 逐日產 snapshot 累積校準資料")
    bf.add_argument("action", choices=["start", "stop", "status", "plan", "reset-failed"])
    bf.add_argument("--coin", default=None, help="幣種（逗號分隔，如 BTC,ETH；預設全部）")
    bf.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（預設 2021-07-01）")
    bf.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    bf.add_argument("--batch-size", type=int, default=30, help="每輪處理天數")
    bf.add_argument("--interval", type=float, default=5.0, help="批次間隔秒數")
    bf.add_argument("--daemon", action="store_true", help="前台持續執行（Ctrl+C 停止）")
    bf.add_argument("--json", action="store_true", help="輸出 JSON")
    bf.add_argument("--data-dir", default=None, help="OHLCV 資料目錄")
    bf.add_argument("--mode", default="offline", choices=["offline", "live"],
                    help="offline=離線（預設）；live=真 Bedrock")
    bf.add_argument("--sample", type=int, default=None,
                    help="抽樣天數（均勻分布跨時間範圍；預設全量）")
    bf.set_defaults(func=cmd_backfill)

    tc = sub.add_parser("train-calibration", help="從 training data 訓練 isotonic calibration model")
    tc.add_argument("--training-dir", default="data/training",
                    help="Training data JSONL 目錄（預設 data/training）")
    tc.add_argument("--data-dir", default="data/data",
                    help="OHLCV CSV 目錄（預設 data/data）")
    tc.add_argument("--out", default="out/model-artifacts/calibration-model.json",
                    help="模型輸出路徑")
    tc.set_defaults(func=cmd_train_calibration)

    lo = sub.add_parser("label-outcomes", help="用 OHLCV T+N outcome 標記 ground truth 方向")
    lo.add_argument("--training-dir", default="data/training",
                    help="Training data JSONL 目錄（預設 data/training）")
    lo.add_argument("--data-dir", default="data/data",
                    help="OHLCV CSV 目錄（預設 data/data）")
    lo.add_argument("--horizon", type=int, default=7,
                    help="T+N 天數（預設 7）")
    lo.add_argument("--threshold", type=float, default=3,
                    help="方向判定門檻百分比（預設 3）")
    lo.set_defaults(func=cmd_label_outcomes)

    sg = sub.add_parser("security-gate", help="投稿前安全掃描：secret / 內網 reference 檢查")
    sg.add_argument("--out", default="out", help="報告輸出目錄（預設 out/）")
    sg.set_defaults(func=cmd_security_gate)

    qa = sub.add_parser("qa-matrix", help="QA mini matrix：5 幣 × 3 題型退化檢查")
    qa.add_argument("--offline", action="store_true", help="使用離線樣本資料（免 AWS）")
    qa.add_argument("--data-dir", default=None, help="OHLCV 資料目錄")
    qa.add_argument("--out", default="out", help="輸出目錄（預設 out/）")
    qa.set_defaults(func=cmd_qa_matrix)

    cal = sub.add_parser("calibrate", help="Historical replay calibration diagnostic")
    cal.add_argument("--coin", choices=COIN_POOL, required=True)
    cal.add_argument("--training-data", type=Path, default=Path("data/training"))
    cal.add_argument("--data-dir", default="data/data", help="OHLCV CSV 資料目錄")
    cal.add_argument("--out", default="out/historical-replay-calibration.json")
    cal.set_defaults(func=cmd_calibrate)

    mh = sub.add_parser("modelhub-train", help="建立 ModelHub calibrator 訓練候選 proposal")
    target = mh.add_mutually_exclusive_group(required=True)
    target.add_argument("--coin", choices=COIN_POOL)
    target.add_argument("--all", action="store_true")
    mh.add_argument("--dry-run", action="store_true")
    mh.add_argument("--training-dir", default="data/training")
    mh.add_argument("--out-dir", default="out/modelhub-proposals")
    mh.add_argument("--req-no", default=None)
    mh.add_argument("--req-no-map", action="append", default=[], metavar="COIN=REQ")
    mh.set_defaults(func=cmd_modelhub_train)

    args = p.parse_args(argv)
    if not hasattr(args, "func"):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
