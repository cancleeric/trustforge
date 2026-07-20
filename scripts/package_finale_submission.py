#!/usr/bin/env python3
"""Package the finale submission artifacts with preflight checks."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ARTIFACTS = ("report.md", "evidence.json", "execution_log.jsonl")
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-]{30,}"),
)


@dataclass(frozen=True)
class PackageResult:
    package_dir: Path
    zip_path: Path
    manifest_path: Path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"required artifact missing: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"required artifact is empty: {path}")


def validate_live_artifacts(source_dir: Path) -> list[Path]:
    files = [source_dir / name for name in REQUIRED_ARTIFACTS]
    for path in files:
        _require_file(path)

    report = _read_text(source_dir / "report.md")
    if "[OFFLINE]" in report:
        raise ValueError("report.md contains [OFFLINE]; run Bedrock live mode before packaging")

    evidence_path = source_dir / "evidence.json"
    try:
        json.loads(_read_text(evidence_path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"evidence.json is not valid JSON: {exc}") from exc

    log = _read_text(source_dir / "execution_log.jsonl")
    if not re.search(r"bedrock|invoke", log, re.I):
        raise ValueError("execution_log.jsonl must show a Bedrock invoke/live model trace")

    return files


def scan_for_secrets(paths: list[Path]) -> None:
    offenders: list[str] = []
    for path in paths:
        text = _read_text(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path))
                break
    if offenders:
        joined = ", ".join(sorted(set(offenders)))
        raise ValueError(f"possible secret material found in submission artifact(s): {joined}")


def current_commit(root: Path = ROOT) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def assert_clean_tracked_tree(root: Path = ROOT) -> None:
    dirty = subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode != 0
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0
    if dirty or staged:
        raise RuntimeError("tracked tree has uncommitted changes; commit or stash before packaging code snapshot")


def write_repo_snapshot(package_dir: Path, root: Path = ROOT) -> Path:
    snapshot_path = package_dir / "repo.tar.gz"
    with snapshot_path.open("wb") as handle:
        subprocess.run(["git", "archive", "--format=tar.gz", "HEAD"], cwd=root, stdout=handle, check=True)
    return snapshot_path


def package_submission(
    source_dir: Path,
    output_dir: Path,
    demo_url: str | None = None,
    require_clean_tree: bool = True,
) -> PackageResult:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    files = validate_live_artifacts(source_dir)
    scan_for_secrets(files)
    if require_clean_tree:
        assert_clean_tracked_tree()

    package_dir = output_dir / "finale-submission"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    copied_files: list[str] = []
    for path in files:
        target = package_dir / path.name
        shutil.copy2(path, target)
        copied_files.append(target.name)

    repo_snapshot = write_repo_snapshot(package_dir)
    copied_files.append(repo_snapshot.name)

    manifest = {
        "commit": current_commit(),
        "demo_url": demo_url or "",
        "source_dir": str(source_dir),
        "artifacts": copied_files,
        "checks": {
            "required_artifacts": "pass",
            "offline_marker": "absent",
            "evidence_json": "valid",
            "bedrock_trace": "present",
            "secret_scan": "pass",
        },
    }
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    zip_path = output_dir / "finale-submission.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.iterdir()):
            archive.write(path, arcname=f"finale-submission/{path.name}")

    return PackageResult(package_dir=package_dir, zip_path=zip_path, manifest_path=manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="out/artifacts/bedrock-live-run")
    parser.add_argument("--output-dir", default="out/submission")
    parser.add_argument("--demo-url", default=None)
    args = parser.parse_args(argv)

    result = package_submission(Path(args.source_dir), Path(args.output_dir), args.demo_url)
    print(f"submission package: {result.zip_path}")
    print(f"manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
