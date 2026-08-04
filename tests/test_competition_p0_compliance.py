"""Competition P0 compliance regressions (#1201, #1202, #1203, #1206).

These tests are intentionally local-only: they do not invoke AWS.  They lock the
code-level guardrails that can be verified from the repository before the final
human/AWS Console checklist is completed.
"""
from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    assert cfg.model_id == ""


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


def test_bedrock_interval_cannot_be_configured_below_one_second(tmp_path) -> None:
    from trustforge.bedrock import BedrockRpsLimiter

    limiter = BedrockRpsLimiter(min_interval=0.01, lock_path=tmp_path / "rps.lock")
    assert limiter.min_interval == 1.0


def test_bedrock_lambda_fails_closed_without_shared_limiter(monkeypatch, tmp_path) -> None:
    from trustforge.bedrock import BedrockRpsLimiter

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "trustforge")
    limiter = BedrockRpsLimiter(lock_path=tmp_path / "rps.lock")
    with pytest.raises(RuntimeError, match="distributed limiter"):
        limiter.acquire()


def test_bedrock_limiter_serializes_two_processes(tmp_path) -> None:
    lock_path = tmp_path / "shared-rps.lock"
    code = (
        "import sys,time; "
        "from trustforge.bedrock import BedrockRpsLimiter; "
        "BedrockRpsLimiter(lock_path=sys.argv[1]).acquire(); "
        "print(time.time(), flush=True)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    timestamps = sorted(float(process.communicate(timeout=5)[0]) for process in processes)
    assert timestamps[1] - timestamps[0] >= 0.9
    assert all(process.returncode == 0 for process in processes)


def test_bedrock_limiter_fails_closed_on_invalid_timestamp(tmp_path) -> None:
    from trustforge.bedrock import BedrockRpsLimiter

    lock_path = tmp_path / "stale.lock"
    lock_path.write_text("not-a-timestamp\n", encoding="utf-8")
    limiter = BedrockRpsLimiter(lock_path=lock_path)
    with pytest.raises(RuntimeError, match="malformed"):
        limiter.acquire()


def test_bedrock_production_lock_path_cannot_be_split_by_env(monkeypatch) -> None:
    from trustforge.bedrock import BedrockRpsLimiter

    monkeypatch.setenv("TRUSTFORGE_BEDROCK_LIMITER_PATH", "/tmp/attacker-selected.lock")
    limiter = BedrockRpsLimiter()
    assert str(limiter._lock_path) == "/tmp/trustforge-bedrock-rps.lock"


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
    # Only scan git-tracked files: the test guards against identifiers that
    # would be committed to the repository, not local build/release artefacts
    # under gitignored directories (out/, .venv, node_modules, …).
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    offenders: list[str] = []
    for rel in tracked:
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="ignore")
        if forbidden.search(text):
            offenders.append(rel)
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
