#!/usr/bin/env python3
"""Automated demo evidence capture for TrustForge finale submission.

Usage:
    python scripts/demo_evidence_capture.py

Outputs:
    out/demo-evidence/*.png — screenshots (desktop + mobile)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEMO_URL = "http://3.106.220.68/"
OUTDIR = Path("out/demo-evidence")

SCENARIOS = [
    ("A1-home-hero", DEMO_URL, 1280, 720),
    ("A1-home-hero-mobile", DEMO_URL, 375, 812),
    ("B1-select-coin", DEMO_URL + "#select", 1280, 720),
    ("B1-select-coin-mobile", DEMO_URL + "#select", 375, 812),
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEMO_URL)
    p.add_argument("--out", type=Path, default=OUTDIR)
    p.add_argument("--mobile-only", action="store_true")
    p.add_argument("--desktop-only", action="store_true")
    args = p.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, url, width, height in SCENARIOS:
            if args.mobile_only and width >= 800:
                continue
            if args.desktop_only and width < 800:
                continue
            page = browser.new_page(viewport={"width": width, "height": height})
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.screenshot(path=out / f"{name}.png", full_page=True)
                print(f"✅ {name} ({width}x{height})")
            except Exception as exc:
                print(f"❌ {name}: {exc}")
            finally:
                page.close()
        browser.close()

    index = {"url": args.url, "screenshots": len(SCENARIOS), "outdir": str(out)}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(f"\nDone: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
