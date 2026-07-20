"""Security gate 模組（CLI 整合入口）。

委託 scripts/security_gate.py 的掃描邏輯。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 加入 scripts/ 到 path 以便直接 import
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def run_security_gate(out_dir: str | None = None) -> int:
    """掃描並產出報告。回傳 0=pass, 1=有 P0。"""
    # 動態 import 避免模組層級副作用
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        from security_gate import scan, write_report  # type: ignore[import]
    finally:
        sys.path.pop(0)

    root = Path(__file__).resolve().parent.parent.parent
    result = scan(root)
    out = Path(out_dir) if out_dir else None
    report_path = write_report(result, out)

    print(f"Security Gate Scan 完成")
    print(f"  掃描檔案：{result.files_scanned}")
    print(f"  發現：P0={result.p0_count}, P1={result.p1_count}, P2={result.p2_count}")
    print(f"  報告：{report_path}")

    if result.p0_count > 0:
        print(f"\n⚠️  P0 發現 {result.p0_count} 項，投稿前必須修正！")
        for f in result.findings:
            if f.severity == "P0":
                print(f"    [{f.category}] {f.file}:{f.line} → {f.match}")
        return 1

    if result.p1_count > 0:
        print(f"\n⚠️  P1 發現 {result.p1_count} 項（內網 reference in 非開發檔），建議修正：")
        for f in result.findings:
            if f.severity == "P1":
                print(f"    {f.file}:{f.line} → {f.match}")

    if result.p0_count == 0:
        print("\n✅ 無 P0 secret leak — 安全 gate 通過")
    return 0
