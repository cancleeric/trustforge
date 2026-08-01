"""投稿前安全掃描 Gate（Issue #205）。

掃描整個 repo，找出：
- P0: Secret（API key / token / private key / .env 含值）
- P1: 內網 reference 出現在非開發設定檔中
- P2: 內網 reference 出現在開發設定檔中

產出：out/security-gate-report.json

用法：
  python scripts/security_gate.py
  python -m trustforge.cli security-gate [--out DIR]
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator


# ── 排除目錄 ─────────────────────────────────────────────────────────
EXCLUDE_DIRS = frozenset([
    "node_modules", ".git", "__pycache__", "out", ".venv",
    "dist", "build", ".cache", "target", ".mypy_cache", ".pytest_cache",
])

# ── 排除特定檔案（自身不該被掃描出來） ─────────────────────────────────
EXCLUDE_FILES = frozenset([
    "scripts/security_gate.py",
    "src/trustforge/security_gate.py",
    "tests/test_security_gate.py",
])

# ── Secret scan 排除目錄（含 dummy token 的測試/deploy 腳本） ─────────
SECRET_RELAXED_DIRS: list[re.Pattern[str]] = [
    re.compile(r"^tests/"),
    re.compile(r"^deploy/"),
    re.compile(r"^scripts/"),
    re.compile(r"^docs/"),
    re.compile(r"^\.kiro/"),
]

# ── 內網 scan 排除的目錄/檔案 pattern（OHLCV 價格數據會匹配 10.x） ─
INTERNAL_NET_EXCLUDE: list[re.Pattern[str]] = [
    re.compile(r"^data/"),        # OHLCV 價格含 10.xxx
    re.compile(r"\.csv$"),        # CSV 數據
    re.compile(r"package-lock\.json$"),  # npm lockfile
    re.compile(r"\.lock$"),       # lockfiles
]

# ── Secret patterns ────────────────────────────────────────────────────
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "hardcoded_secret",
        re.compile(
            r"""(AKIA|sk-|ghp_|ghs_|token|secret|password|api.?key)\s*[=:]\s*['"][^'"]{8,}""",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN.*PRIVATE KEY-----"),
    ),
]

# ── 內網 patterns ─────────────────────────────────────────────────────
# 需要至少兩個 octet (10.x.y) 才算真的 private IP，避免 CSS 的 10.5px
INTERNAL_NET_PATTERN = re.compile(
    r"("
    r"localhost:\d+"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|\w+\.local[:/]"
    r")",
    re.IGNORECASE,
)

# ── .env 有值 pattern ─────────────────────────────────────────────────
ENV_VALUE_PATTERN = re.compile(
    r"^[A-Z_][A-Z0-9_]*\s*=\s*\S+",
    re.MULTILINE,
)

# ── 「開發設定」檔案 pattern（這些出現內網 reference 只算 P2） ─────────
DEV_FILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|/)README", re.IGNORECASE),
    re.compile(r"(^|/)AGENTS\.md$", re.IGNORECASE),
    re.compile(r"(^|/)ROADMAP", re.IGNORECASE),
    re.compile(r"(^|/)CHANGELOG", re.IGNORECASE),
    re.compile(r"(^|/)docs/", re.IGNORECASE),
    re.compile(r"(^|/)\.kiro/", re.IGNORECASE),
    re.compile(r"(^|/)scripts/", re.IGNORECASE),
    re.compile(r"(^|/)tests/", re.IGNORECASE),
    re.compile(r"(^|/)demo/", re.IGNORECASE),
    re.compile(r"\.example$", re.IGNORECASE),
    re.compile(r"(^|/)docker-compose", re.IGNORECASE),
    re.compile(r"(^|/)Makefile$", re.IGNORECASE),
    re.compile(r"(^|/)\.env\.example$", re.IGNORECASE),
    re.compile(r"(^|/)apprunner\.yaml$", re.IGNORECASE),
    re.compile(r"(^|/)Dockerfile$", re.IGNORECASE),
]


def _is_known_false_positive_secret(relpath: str, line: str, pattern_name: str) -> bool:
    """Return True for UI labels / dummy test payloads that look like keys."""
    normalized = relpath.replace("\\", "/")
    if (
        pattern_name == "private_key"
        and normalized == "src/trustforge/hermes_audit_contracts.py"
        and "BEGIN(?: [A-Z]+)? PRIVATE KEY" in line
    ):
        return True
    if pattern_name != "hardcoded_secret":
        return False
    if normalized == "frontend/src/hermes/hermesI18n.tsx" and (
        "Admin Token" in line or "Gas token" in line
    ):
        return True
    return False


@dataclass
class Finding:
    severity: str  # P0, P1, P2
    category: str  # secret / internal_net / env_value
    file: str
    line: int
    match: str
    pattern_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    p0_count: int = 0
    p1_count: int = 0
    p2_count: int = 0

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.severity == "P0":
            self.p0_count += 1
        elif finding.severity == "P1":
            self.p1_count += 1
        else:
            self.p2_count += 1

    def to_dict(self) -> dict:
        return {
            "summary": {
                "files_scanned": self.files_scanned,
                "total_findings": len(self.findings),
                "p0_count": self.p0_count,
                "p1_count": self.p1_count,
                "p2_count": self.p2_count,
                "pass": self.p0_count == 0,
            },
            "findings": [f.to_dict() for f in self.findings],
        }


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS


def _is_dev_file(relpath: str) -> bool:
    return any(p.search(relpath) for p in DEV_FILE_PATTERNS)


def _is_binary(filepath: Path) -> bool:
    """粗略判斷是否為二進位檔案。"""
    try:
        chunk = filepath.read_bytes()[:8192]
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


def _iter_files(root: Path) -> Iterator[Path]:
    """遞迴列出檔案，排除黑名單目錄。"""
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if _is_excluded_dir(entry.name):
                continue
            yield from _iter_files(entry)
        elif entry.is_file():
            yield entry


def _is_env_file(filepath: Path) -> bool:
    """判斷是否為 .env 類型檔案（含值 = 可能洩密）。"""
    name = filepath.name.lower()
    # .env, .env.local, .env.production 等，但排除 .env.example
    if name == ".env" or (name.startswith(".env.") and "example" not in name):
        return True
    return False


def scan(root: Path | None = None) -> ScanResult:
    """掃描 repo root 下所有檔案。"""
    if root is None:
        root = Path(__file__).resolve().parent.parent
    root = root.resolve()

    result = ScanResult()

    for filepath in _iter_files(root):
        # 跳過二進位
        if _is_binary(filepath):
            continue

        relpath = str(filepath.relative_to(root))

        # 跳過自身定義檔
        if relpath.replace("\\", "/") in EXCLUDE_FILES:
            continue

        result.files_scanned += 1

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        lines = content.splitlines()

        # ── .env 檔案含值 → P0 ────────────────────────────────────
        if _is_env_file(filepath):
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if ENV_VALUE_PATTERN.match(stripped):
                    result.add(Finding(
                        severity="P0",
                        category="env_value",
                        file=relpath,
                        line=i,
                        match=_truncate(stripped),
                        pattern_name="dotenv_with_value",
                    ))
            continue  # .env 不需要再做其他 pattern 掃描

        # ── Secret patterns → P0 (relaxed dirs → P2) ─────────────
        is_secret_relaxed = any(p.search(relpath) for p in SECRET_RELAXED_DIRS)
        for i, line in enumerate(lines, 1):
            for pattern_name, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    if _is_known_false_positive_secret(relpath, line, pattern_name):
                        continue
                    exact_dummy_fixture = (
                        relpath.replace("\\", "/")
                        == "frontend/src/lib/adminApi.test.ts"
                        and re.fullmatch(
                            r"\s*api_key: ['\"]must-not-be-accepted['\"],\s*",
                            line,
                        )
                        is not None
                    )
                    severity = "P2" if is_secret_relaxed or exact_dummy_fixture else "P0"
                    result.add(Finding(
                        severity=severity,
                        category="secret",
                        file=relpath,
                        line=i,
                        match=_truncate(m.group(0)),
                        pattern_name=pattern_name,
                    ))

        # ── 內網 reference → P1/P2 ────────────────────────────────
        # 跳過不適合做內網掃描的檔案（OHLCV 數據等）
        skip_internal = any(p.search(relpath) for p in INTERNAL_NET_EXCLUDE)
        if not skip_internal:
            is_dev = _is_dev_file(relpath)
            for i, line in enumerate(lines, 1):
                m = INTERNAL_NET_PATTERN.search(line)
                if m:
                    severity = "P2" if is_dev else "P1"
                    result.add(Finding(
                        severity=severity,
                        category="internal_net",
                        file=relpath,
                        line=i,
                        match=_truncate(m.group(0)),
                        pattern_name="internal_network_ref",
                    ))

    return result


def _truncate(s: str, max_len: int = 120) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def write_report(result: ScanResult, out_dir: Path | None = None) -> Path:
    """寫入 JSON 報告到 out/security-gate-report.json。"""
    if out_dir is None:
        out_dir = Path(__file__).resolve().parent.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "security-gate-report.json"
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


def main(out_dir: str | None = None) -> int:
    """CLI 入口。回傳 0=pass, 1=有 P0。"""
    root = Path(__file__).resolve().parent.parent
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

    print("\n✅ 無 P0 secret leak — 安全 gate 通過" if result.p0_count == 0 else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
