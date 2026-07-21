#!/usr/bin/env python3
"""Package TrustForge finale submission artifacts.

The packager is intentionally conservative: it refuses missing deliverables and
obvious secrets before writing the zip.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


REQUIRED_FILES = ("report.md", "evidence.json", "execution_log.jsonl")
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[^\s'\"]+"),
    re.compile(r"(?i)[\"']?(api[_-]?key|secret|token)[\"']?\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def validate_artifact_dir(artifact_dir: Path) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_FILES:
        if not (artifact_dir / name).is_file():
            errors.append(f"missing required artifact: {name}")
    report = artifact_dir / "report.md"
    if report.is_file() and "[OFFLINE]" in _read_text(report):
        errors.append("report.md contains [OFFLINE] marker")
    evidence = artifact_dir / "evidence.json"
    if evidence.is_file():
        try:
            json.loads(_read_text(evidence))
        except json.JSONDecodeError as exc:
            errors.append(f"evidence.json is invalid JSON: {exc}")
    for path in artifact_dir.rglob("*"):
        if not path.is_file():
            continue
        text = _read_text(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"possible secret in {path.relative_to(artifact_dir)}")
                break
    return errors


def package_submission(artifact_dir: Path, output_zip: Path, extra_files: list[Path]) -> None:
    errors = validate_artifact_dir(artifact_dir)
    if errors:
        raise SystemExit("\\n".join(errors))
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in REQUIRED_FILES:
            zf.write(artifact_dir / name, f"artifacts/{name}")
        for extra in extra_files:
            if extra.is_file():
                zf.write(extra, extra.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("out/artifacts/trustforge-submission.zip"))
    parser.add_argument("--extra", type=Path, action="append", default=[])
    args = parser.parse_args()
    package_submission(args.artifact_dir, args.out, args.extra)
    print(f"submission package written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
