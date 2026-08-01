"""Security gate 掃描功能測試。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import sys

# 讓 import 找到 scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from security_gate import Finding, ScanResult, scan, write_report  # type: ignore


def _write(tmp: Path, relpath: str, content: str) -> None:
    p = tmp / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestSecretDetection:
    def test_aws_access_key(self, tmp_path: Path) -> None:
        _write(tmp_path, "config.py", 'KEY = "AKIAIOSFODNN7EXAMPLE"')
        result = scan(tmp_path)
        assert result.p0_count >= 1
        assert any(f.pattern_name == "aws_access_key" for f in result.findings)

    def test_hardcoded_token(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "token = 'ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456'")
        result = scan(tmp_path)
        assert result.p0_count >= 1
        assert any(f.pattern_name == "hardcoded_secret" for f in result.findings)

    def test_private_key(self, tmp_path: Path) -> None:
        _write(tmp_path, "key.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n")
        result = scan(tmp_path)
        assert result.p0_count >= 1
        assert any(f.pattern_name == "private_key" for f in result.findings)

    def test_env_file_with_value(self, tmp_path: Path) -> None:
        _write(tmp_path, ".env", "AWS_SECRET=mysupersecretvalue123\nDB_HOST=localhost")
        result = scan(tmp_path)
        assert result.p0_count >= 1
        assert any(f.category == "env_value" for f in result.findings)

    def test_env_example_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, ".env.example", "AWS_SECRET=<your_secret_here>")
        result = scan(tmp_path)
        # .env.example should not be treated as a real .env
        assert not any(f.category == "env_value" for f in result.findings)

    def test_clean_file_no_findings(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.py", "print('hello world')\n")
        result = scan(tmp_path)
        assert result.p0_count == 0
        assert result.p1_count == 0

    def test_known_i18n_token_labels_are_not_p0(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "frontend/src/hermes/hermesI18n.tsx",
            "shipGateNeedToken: '請先到管理頁解鎖 Admin Token。'\n"
            "gasLabel: 'Gas token'\n",
        )
        result = scan(tmp_path)
        assert result.p0_count == 0

    def test_frontend_test_dummy_api_key_is_relaxed(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "frontend/src/lib/adminApi.test.ts",
            "const payload = {api_key: 'must-not-be-accepted'}\n",
        )
        result = scan(tmp_path)
        assert result.p0_count == 0
        assert result.p2_count >= 1


class TestInternalNetDetection:
    def test_localhost_in_source(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/server.py", "url = 'http://localhost:8080/api'")
        result = scan(tmp_path)
        # src/ 非 dev file pattern → P1
        assert result.p1_count >= 1

    def test_localhost_in_readme(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md", "Visit http://localhost:3000 for local dev")
        result = scan(tmp_path)
        # README → dev file → P2
        assert result.p2_count >= 1
        assert result.p1_count == 0

    def test_private_ip(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/config.py", "HOST = '192.168.1.100'")
        result = scan(tmp_path)
        assert any(f.category == "internal_net" for f in result.findings)

    def test_dot_local_domain(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/app.py", "url = 'http://myhost.local:3030/api'")
        result = scan(tmp_path)
        assert any(f.category == "internal_net" for f in result.findings)

    def test_scripts_dir_is_dev(self, tmp_path: Path) -> None:
        _write(tmp_path, "scripts/deploy.sh", "curl http://localhost:8080/healthz")
        result = scan(tmp_path)
        assert result.p2_count >= 1
        assert result.p1_count == 0


class TestExclusions:
    def test_git_dir_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path, ".git/config", "token = 'ghp_abcdefghijklmnop12345678'")
        result = scan(tmp_path)
        assert result.p0_count == 0

    def test_node_modules_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path, "node_modules/pkg/index.js", "secret = 'sk-verylongsecretkey12345'")
        result = scan(tmp_path)
        assert result.p0_count == 0

    def test_venv_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path, ".venv/lib/site.py", "password = 'supersecretpassword123'")
        result = scan(tmp_path)
        assert result.p0_count == 0


class TestReportOutput:
    def test_write_report_creates_json(self, tmp_path: Path) -> None:
        result = ScanResult()
        result.add(Finding(
            severity="P0", category="secret",
            file="test.py", line=1, match="token='abc'",
            pattern_name="hardcoded_secret",
        ))
        report_path = write_report(result, tmp_path)
        assert report_path.exists()
        import json
        data = json.loads(report_path.read_text())
        assert data["summary"]["p0_count"] == 1
        assert data["summary"]["pass"] is False

    def test_clean_scan_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "clean.py", "x = 42\n")
        result = scan(tmp_path)
        report_path = write_report(result, tmp_path / "out")
        import json
        data = json.loads(report_path.read_text())
        assert data["summary"]["pass"] is True
