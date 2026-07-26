#!/usr/bin/env python3
"""量測台灣監管來源的實際 coverage（issue #385 階段 5）。

打真實官方端點，逐源回報：原始筆數、通過加密關鍵字閘門的筆數、
最新一筆的可見時間。用來決定

- 是否把 references 頁的台灣來源翻成 ✅
- 是否有足夠 coverage 加入 Radar 台灣監管維度

⚠️ 這支腳本會發真實請求到政府站，**不要**放進 CI 或高頻排程。
人工執行、確認 coverage 變化時才跑。

用法：
    PYTHONPATH=src python scripts/measure_taiwan_regulatory_coverage.py
    PYTHONPATH=src python scripts/measure_taiwan_regulatory_coverage.py --json

⚠️ Python ≥3.13 預設開啟 `ssl.VERIFY_X509_STRICT`，會擋掉 fsc.gov.tw 與
tpex.org.tw 的憑證（缺 Subject Key Identifier）。請用 3.11／3.12 執行，
與生產 `python:3.12-slim` 一致。見 discovery 文件地雷 4。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustforge.ingestion.taiwan_regulatory import (  # noqa: E402
    TaiwanRegulatoryUnavailable,
    build_taiwan_regulatory_sources,
)


def measure() -> list[dict]:
    rows: list[dict] = []
    for source in build_taiwan_regulatory_sources():
        row: dict = {
            "source": source.name,
            "agency": source._agency,
            "endpoints": list(source._endpoints),
            "history_backfillable": source._history_backfillable,
            "url_kind": source._url_kind,
        }
        try:
            docs = source.fetch("crypto regulation")
        except TaiwanRegulatoryUnavailable as exc:
            row.update(
                status="unavailable", passed_gate=0, error=str(exc), latest=None
            )
        except Exception as exc:  # noqa: BLE001 - 量測腳本要看到所有失敗
            row.update(
                status="error",
                passed_gate=0,
                error=f"{type(exc).__name__}: {exc}",
                latest=None,
            )
        else:
            latest = max((d.ts for d in docs), default=0.0)
            row.update(
                status="ok",
                passed_gate=len(docs),
                # 標題命中 ＝ 高精準（實測全為真正的 VASP／虛擬資產監管事件）；
                # 僅內文命中 ＝ 低精準（多為新聞彙編、記者會等雜訊）。
                title_hits=sum(
                    1 for d in docs if d.meta.get("gate_match") == "title"
                ),
                body_only=sum(
                    1 for d in docs if d.meta.get("gate_match") == "body"
                ),
                degraded=source.last_degraded,
                truncated=source.last_truncated,
                latest=(
                    datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
                    if latest
                    else None
                ),
                samples=[d.text.splitlines()[0][:70] for d in docs[:3]],
            )
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    parser.add_argument("--verbose", action="store_true", help="顯示降級 WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.verbose else logging.ERROR,
        format="  [%(levelname)s] %(message)s",
    )

    rows = measure()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    total = sum(r["passed_gate"] for r in rows)
    total_title = sum(r.get("title_hits", 0) for r in rows)
    print(f"量測時間：{datetime.now(timezone.utc).isoformat()}")
    print(f"{'來源':14} {'狀態':11} {'通過':>5} {'標題':>5} {'內文':>5}  最新可見時間")
    print("-" * 78)
    for row in rows:
        latest = (row["latest"] or "—")[:10]
        print(
            f"{row['source']:14} {row['status']:11} {row['passed_gate']:>5} "
            f"{row.get('title_hits', 0):>5} {row.get('body_only', 0):>5}  {latest}"
        )
        for sample in row.get("samples", []):
            print(f"                 · {sample}")
        if row.get("error"):
            print(f"                 ! {row['error'][:80]}")
    print("-" * 78)
    print(f"{'合計':14} {'':11} {total:>5} {total_title:>5}")
    print()
    print("『標題』＝標題即為加密監管事件（高精準）；『內文』＝僅內文順帶提及。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
