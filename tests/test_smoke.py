"""tests/test_smoke.py — Bedrock smoke test 的單元測試（不需真實 AWS）。"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _ensure_boto3_mockable(monkeypatch):
    """Ensure boto3 and botocore are importable (mock them if not installed)."""
    try:
        import boto3  # noqa: F401
        import botocore.config  # noqa: F401
    except ImportError:
        # Create mock botocore.config.Config first
        fake_botocore = types.ModuleType("botocore")
        fake_botocore_config = types.ModuleType("botocore.config")

        class FakeConfig:
            def __init__(self, **kwargs):
                pass

        fake_botocore_config.Config = FakeConfig
        fake_botocore.config = fake_botocore_config
        monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
        monkeypatch.setitem(sys.modules, "botocore.config", fake_botocore_config)

        # Create mock boto3
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = MagicMock()
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)


def test_smoke_fails_without_model_id(monkeypatch, tmp_path):
    """BEDROCK_MODEL_ID 未設時 smoke 回傳 1 並寫 artifact。"""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)

    from trustforge.smoke import run_smoke

    rc = run_smoke(out_dir=str(tmp_path))
    assert rc == 1
    artifact = json.loads((tmp_path / "bedrock-smoke-artifact.json").read_text())
    assert artifact["success"] is False
    assert artifact["checks"]["model_id"]["passed"] is False


def test_smoke_fails_without_region(monkeypatch, tmp_path):
    """AWS_REGION 未設時 smoke 回傳 1。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.delenv("AWS_REGION", raising=False)

    from trustforge.smoke import run_smoke

    rc = run_smoke(out_dir=str(tmp_path))
    assert rc == 1
    artifact = json.loads((tmp_path / "bedrock-smoke-artifact.json").read_text())
    assert artifact["success"] is False
    assert artifact["checks"]["region"]["passed"] is False


def test_smoke_fails_on_invoke_error(monkeypatch, tmp_path):
    """Bedrock invoke 失敗時 smoke 回傳 1 並記錄錯誤。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    class FakeClient:
        def converse(self, **kwargs):
            raise RuntimeError("no credentials")

    def fake_boto3_client(*args, **kwargs):
        return FakeClient()

    import boto3
    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    from trustforge.smoke import run_smoke

    rc = run_smoke(out_dir=str(tmp_path))
    assert rc == 1
    artifact = json.loads((tmp_path / "bedrock-smoke-artifact.json").read_text())
    assert artifact["success"] is False
    assert artifact["checks"]["invoke"]["passed"] is False
    assert "no credentials" in artifact["checks"]["invoke"]["error"]


def test_smoke_succeeds_with_mock(monkeypatch, tmp_path):
    """正常回應時 smoke 回傳 0 並寫成功 artifact。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    class FakeClient:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [{"text": "BEDROCK_SMOKE_OK"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 5},
            }

    def fake_boto3_client(*args, **kwargs):
        return FakeClient()

    import boto3
    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    from trustforge.smoke import run_smoke

    rc = run_smoke(out_dir=str(tmp_path))
    assert rc == 0
    artifact = json.loads((tmp_path / "bedrock-smoke-artifact.json").read_text())
    assert artifact["success"] is True
    assert artifact["checks"]["invoke"]["input_tokens"] == 10
    assert artifact["checks"]["invoke"]["output_tokens"] == 5
    assert "[OFFLINE]" not in artifact["checks"]["invoke"]["response_text"]


def test_smoke_detects_offline_placeholder(monkeypatch, tmp_path):
    """回應含 [OFFLINE] 時 smoke 應回傳 1。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    class FakeClient:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [{"text": "[OFFLINE] would answer here"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 5},
            }

    def fake_boto3_client(*args, **kwargs):
        return FakeClient()

    import boto3
    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    from trustforge.smoke import run_smoke

    rc = run_smoke(out_dir=str(tmp_path))
    assert rc == 1
    artifact = json.loads((tmp_path / "bedrock-smoke-artifact.json").read_text())
    assert artifact["success"] is False
    assert artifact["checks"]["no_offline_placeholder"]["passed"] is False
