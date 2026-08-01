"""Competition P0 compliance regressions (#1201, #1202, #1203, #1206).

These tests are intentionally local-only: they do not invoke AWS.  They lock the
code-level guardrails that can be verified from the repository before the final
human/AWS Console checklist is completed.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

from trustforge import analysis_plan, sagemaker_client, ssm_params
from trustforge.bedrock import BedrockClient, BedrockConfig

ROOT = Path(__file__).resolve().parents[1]


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


def test_bedrock_defaults_use_competition_us_region_and_profile(monkeypatch) -> None:
    """#1201: zero-env defaults must not fall back to ap/au profiles."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)

    cfg = BedrockConfig()

    assert cfg.region == "us-east-1"
    assert cfg.stance_model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert cfg.model_id == "us.anthropic.claude-sonnet-4-6"


def test_other_aws_clients_default_to_competition_region() -> None:
    """#1201: SageMaker / SSM / analysis planner defaults stay aligned."""
    assert sagemaker_client._DEFAULT_REGION == "us-east-1"
    assert 'values.get("AWS_REGION", "us-east-1")' in inspect.getsource(analysis_plan)
    assert 'os.getenv("AWS_REGION", "us-east-1")' in inspect.getsource(ssm_params)


def test_bedrock_complete_enforces_one_rps_min_interval() -> None:
    """#1203: consecutive Bedrock narrative calls are separated by >=1s."""
    clock = _FakeClock()
    from trustforge.bedrock import BedrockRpsLimiter

    client = BedrockClient(
        config=BedrockConfig(region="us-east-1", model_id="fake-model"),
        offline=False,
        rps_limiter=BedrockRpsLimiter(
            monotonic=clock.monotonic, sleep=clock.sleep
        ),
    )
    runtime = _FakeRuntime()
    client._client = runtime

    client.complete("system", "prompt 1")
    client.complete("system", "prompt 2")

    assert runtime.calls == 2
    assert clock.sleeps == [1.0]


def test_bedrock_stance_and_extraction_share_one_rps_guard() -> None:
    """#1203: stance/extraction runtime also goes through the same Bedrock throttle."""
    clock = _FakeClock()
    from trustforge.bedrock import BedrockRpsLimiter

    client = BedrockClient(
        config=BedrockConfig(
            region="us-east-1",
            model_id="fake-model",
            stance_model_id="fake-stance-model",
        ),
        offline=False,
        stance_offline=False,
        rps_limiter=BedrockRpsLimiter(
            monotonic=clock.monotonic, sleep=clock.sleep
        ),
    )
    client._client = _FakeRuntime()
    stance_runtime = MagicMock()
    stance_runtime.converse.return_value = {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"name": "classify_stance", "input": {"label": "neutral"}}}
                ]
            }
        },
        "usage": {"inputTokens": 1, "outputTokens": 1},
    }
    client._stance_client = stance_runtime

    client.complete("system", "prompt 1")
    assert client.classify_stance_strict("a", "b") == "neutral"

    assert clock.sleeps == [1.0]


def test_tracked_repository_contains_no_raw_competition_infra_identifiers() -> None:
    """#1202: tracked/public paths must not expose real account, IP, or instance IDs."""
    forbidden = re.compile("|".join(
        [
            "795" + "930" + "814" + "369",
            r"13\.211\.110\.218",
            r"172\.31\.27\.136",
            "i-0152" + "b703" + "68358a81c",
        ]
    ))
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="ignore")
        if forbidden.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_human_console_audit_checklist_is_tracked() -> None:
    """#1206: non-code AWS/data compliance items must have a tracked checklist."""
    checklist = ROOT / "docs" / "competition" / "P0-HUMAN-CONSOLE-AUDIT-CHECKLIST.md"
    text = checklist.read_text(encoding="utf-8")
    for item in (
        "13 類禁止資料",
        "EC2",
        "SageMaker",
        "Bedrock model access",
        "人工確認",
    ):
        assert item in text
