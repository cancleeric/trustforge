"""Tests for SageMaker training backend client (#704, #707)."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trustforge.training_backend import (
    TrainingBackend,
    TrainingBackendConfigError,
    resolve_training_backend,
)
from trustforge.sagemaker_client import SageMakerBackend
from trustforge.modelhub_backend import ModelHubBackend


# ═══════════════════════════════════════════════════════════════════════════════
# TrainingBackend Protocol 驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrainingBackendProtocol:
    def test_sagemaker_implements_protocol(self):
        backend = SageMakerBackend(offline=True)
        assert isinstance(backend, TrainingBackend)

    def test_modelhub_implements_protocol(self):
        backend = ModelHubBackend(offline=True)
        assert isinstance(backend, TrainingBackend)

    def test_backend_id_sagemaker(self):
        assert SageMakerBackend(offline=True).backend_id == "sagemaker"

    def test_backend_id_modelhub(self):
        assert ModelHubBackend(offline=True).backend_id == "modelhub"


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_training_backend
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveTrainingBackend:
    def test_default_is_modelhub(self, monkeypatch):
        monkeypatch.delenv("TRAINING_BACKEND", raising=False)
        backend = resolve_training_backend(offline=True)
        assert isinstance(backend, ModelHubBackend)

    def test_explicit_modelhub(self, monkeypatch):
        monkeypatch.setenv("TRAINING_BACKEND", "modelhub")
        backend = resolve_training_backend(offline=True)
        assert isinstance(backend, ModelHubBackend)

    def test_explicit_sagemaker(self, monkeypatch):
        monkeypatch.setenv("TRAINING_BACKEND", "sagemaker")
        backend = resolve_training_backend(offline=True)
        assert isinstance(backend, SageMakerBackend)

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TRAINING_BACKEND", "SageMaker")
        backend = resolve_training_backend(offline=True)
        assert isinstance(backend, SageMakerBackend)

    def test_unsupported_raises(self, monkeypatch):
        monkeypatch.setenv("TRAINING_BACKEND", "openai")
        with pytest.raises(TrainingBackendConfigError, match="Unsupported"):
            resolve_training_backend(offline=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — offline 模式
# ═══════════════════════════════════════════════════════════════════════════════


class TestSageMakerOffline:
    def test_offline_does_not_import_boto3(self):
        """offline=True 不應觸發 boto3 匯入。"""
        backend = SageMakerBackend(offline=True)
        assert backend._sm_client is None
        assert backend._s3_client is None

    def test_offline_trigger_returns_placeholder(self):
        backend = SageMakerBackend(offline=True)
        job_name = backend.trigger_training("BTC", [{"confidence": 0.8, "hit": True}])
        assert job_name == "sagemaker-offline-btc"

    def test_offline_poll_returns_completed(self):
        backend = SageMakerBackend(offline=True)
        result = backend.poll_result("some-job")
        assert result["status"] == "completed"

    def test_offline_download_creates_placeholder(self, tmp_path):
        backend = SageMakerBackend(offline=True)
        model_path = backend.download_artifact("some-job", tmp_path / "model")
        assert model_path.exists()
        data = json.loads(model_path.read_text())
        assert data["offline"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — 設定驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestSageMakerConfig:
    def test_missing_bucket_raises(self, monkeypatch):
        monkeypatch.delenv("SAGEMAKER_TRAINING_BUCKET", raising=False)
        backend = SageMakerBackend(bucket="", role_arn="arn:aws:iam::123:role/test")
        with pytest.raises(TrainingBackendConfigError, match="BUCKET"):
            backend.trigger_training("BTC", [{"a": 1}])

    def test_missing_role_raises(self, monkeypatch):
        monkeypatch.delenv("SAGEMAKER_ROLE_ARN", raising=False)
        backend = SageMakerBackend(bucket="my-bucket", role_arn="")
        with pytest.raises(TrainingBackendConfigError, match="ROLE_ARN"):
            backend.trigger_training("BTC", [{"a": 1}])

    def test_env_vars_used(self, monkeypatch):
        monkeypatch.setenv("SAGEMAKER_TRAINING_BUCKET", "env-bucket")
        monkeypatch.setenv("SAGEMAKER_ROLE_ARN", "arn:aws:iam::123:role/env-role")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        monkeypatch.setenv("SAGEMAKER_INSTANCE_TYPE", "ml.m5.xlarge")
        monkeypatch.setenv("SAGEMAKER_USE_SPOT", "true")

        backend = SageMakerBackend()
        assert backend.bucket == "env-bucket"
        assert backend.role_arn == "arn:aws:iam::123:role/env-role"
        assert backend.region == "us-west-2"
        assert backend.instance_type == "ml.m5.xlarge"
        assert backend.use_spot is True

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("SAGEMAKER_TRAINING_BUCKET", "env-bucket")
        backend = SageMakerBackend(bucket="param-bucket", role_arn="arn:role", offline=True)
        assert backend.bucket == "param-bucket"


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — trigger_training (mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSageMakerTrigger:
    def _make_backend(self) -> tuple[SageMakerBackend, MagicMock, MagicMock]:
        backend = SageMakerBackend(
            bucket="test-bucket",
            role_arn="arn:aws:iam::123:role/test",
            region="ap-southeast-2",
        )
        mock_sm = MagicMock()
        mock_s3 = MagicMock()
        backend._sm_client = mock_sm
        backend._s3_client = mock_s3
        return backend, mock_sm, mock_s3

    def test_trigger_uploads_and_creates_job(self):
        backend, mock_sm, mock_s3 = self._make_backend()
        rows = [{"confidence": 0.7, "hit": True}, {"confidence": 0.3, "hit": False}]

        job_name = backend.trigger_training("BTC", rows)

        # S3 upload 被呼叫
        assert mock_s3.put_object.call_count == 1
        put_call = mock_s3.put_object.call_args
        assert put_call.kwargs["Bucket"] == "test-bucket"
        assert "BTC" in put_call.kwargs["Key"]
        assert put_call.kwargs["ContentType"] == "application/x-ndjson"

        # SageMaker create_training_job 被呼叫
        assert mock_sm.create_training_job.call_count == 1
        create_call = mock_sm.create_training_job.call_args
        assert create_call.kwargs["TrainingJobName"] == job_name
        assert create_call.kwargs["RoleArn"] == "arn:aws:iam::123:role/test"
        assert "BTC" in create_call.kwargs["HyperParameters"]["coin"]

    def test_trigger_job_name_contains_coin(self):
        backend, _, _ = self._make_backend()
        job_name = backend.trigger_training("ETH", [{"x": 1}])
        assert "eth" in job_name.lower()
        assert job_name.startswith("trustforge-calibrator-")

    def test_trigger_spot_instance(self):
        backend, mock_sm, mock_s3 = self._make_backend()
        backend.use_spot = True
        backend.trigger_training("SOL", [{"x": 1}])

        create_call = mock_sm.create_training_job.call_args
        assert create_call.kwargs["EnableManagedSpotTraining"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — poll_result (mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSageMakerPoll:
    def _make_backend(self, responses: list[dict]) -> SageMakerBackend:
        backend = SageMakerBackend(
            bucket="b", role_arn="r", offline=False
        )
        mock_sm = MagicMock()
        mock_sm.describe_training_job.side_effect = responses
        backend._sm_client = mock_sm
        backend._sleep = lambda _: None  # 不實際 sleep
        backend._monotonic = self._make_clock()
        return backend

    def _make_clock(self):
        """模擬時間：每次呼叫遞增 1 秒。"""
        t = [0.0]

        def clock():
            t[0] += 1.0
            return t[0]

        return clock

    def test_poll_completed(self):
        backend = self._make_backend([
            {"TrainingJobStatus": "InProgress"},
            {"TrainingJobStatus": "Completed", "ModelArtifacts": {"S3ModelArtifacts": "s3://b/model.tar.gz"}},
        ])
        result = backend.poll_result("job-1", max_wait=60)
        assert result["status"] == "completed"
        assert result["artifact_path"] == "s3://b/model.tar.gz"

    def test_poll_failed(self):
        backend = self._make_backend([
            {"TrainingJobStatus": "Failed", "FailureReason": "OOM"},
        ])
        result = backend.poll_result("job-1")
        assert result["status"] == "failed"
        assert "OOM" in result["failure_reason"]

    def test_poll_timeout(self):
        # 時鐘每呼叫 +1s，max_wait=2 → 第三次超時
        backend = self._make_backend([
            {"TrainingJobStatus": "InProgress"},
            {"TrainingJobStatus": "InProgress"},
            {"TrainingJobStatus": "InProgress"},
        ])
        result = backend.poll_result("job-1", max_wait=2)
        assert result["status"] == "timeout"

    def test_poll_stopped(self):
        backend = self._make_backend([
            {"TrainingJobStatus": "Stopped"},
        ])
        result = backend.poll_result("job-1")
        assert result["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — download_artifact (mocked)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_model_tar_gz(model_data: dict) -> bytes:
    """建立一個含 model.json 的 model.tar.gz。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = json.dumps(model_data).encode("utf-8")
        info = tarfile.TarInfo(name="model.json")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestSageMakerDownload:
    def _make_backend(self, describe_resp: dict, s3_body: bytes) -> tuple[SageMakerBackend, Path]:
        backend = SageMakerBackend(bucket="b", role_arn="r")
        mock_sm = MagicMock()
        mock_sm.describe_training_job.return_value = describe_resp
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(s3_body)}
        backend._sm_client = mock_sm
        backend._s3_client = mock_s3
        return backend

    def test_download_extracts_model_json(self, tmp_path):
        model_data = {
            "points": [{"confidence": 0.3, "calibrated": 0.25}, {"confidence": 0.8, "calibrated": 0.75}],
            "trained_at": "2026-07-26T00:00:00+00:00",
            "sample_count": 100,
        }
        tar_bytes = _make_model_tar_gz(model_data)

        backend = self._make_backend(
            describe_resp={
                "ModelArtifacts": {"S3ModelArtifacts": "s3://my-bucket/output/model.tar.gz"}
            },
            s3_body=tar_bytes,
        )

        result_path = backend.download_artifact("job-1", tmp_path / "artifact")
        assert result_path.exists()
        loaded = json.loads(result_path.read_text())
        assert loaded["points"] == model_data["points"]
        assert loaded["sample_count"] == 100

    def test_download_no_model_json_raises(self, tmp_path):
        # tar.gz without model.json
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            content = b"not a model"
            info = tarfile.TarInfo(name="other.txt")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_bytes = buf.getvalue()

        backend = self._make_backend(
            describe_resp={
                "ModelArtifacts": {"S3ModelArtifacts": "s3://b/output/model.tar.gz"}
            },
            s3_body=tar_bytes,
        )

        with pytest.raises(TrainingBackendConfigError, match="model.json"):
            backend.download_artifact("job-1", tmp_path / "artifact")

    def test_download_no_artifact_path_raises(self, tmp_path):
        backend = self._make_backend(
            describe_resp={"ModelArtifacts": {}},
            s3_body=b"",
        )
        with pytest.raises(TrainingBackendConfigError, match="ModelArtifacts"):
            backend.download_artifact("job-1", tmp_path / "artifact")

    def test_download_invalid_s3_uri_raises(self, tmp_path):
        backend = self._make_backend(
            describe_resp={
                "ModelArtifacts": {"S3ModelArtifacts": "not-s3-uri"}
            },
            s3_body=b"",
        )
        with pytest.raises(TrainingBackendConfigError, match="Invalid S3"):
            backend.download_artifact("job-1", tmp_path / "artifact")


# ═══════════════════════════════════════════════════════════════════════════════
# SageMakerBackend — training image
# ═══════════════════════════════════════════════════════════════════════════════


class TestSageMakerTrainingImage:
    def test_ap_southeast_2(self):
        backend = SageMakerBackend(bucket="b", role_arn="r", region="ap-southeast-2", offline=True)
        image = backend._training_image()
        assert "783357654285" in image
        assert "ap-southeast-2" in image
        assert "sagemaker-scikit-learn" in image

    def test_us_east_1(self):
        backend = SageMakerBackend(bucket="b", role_arn="r", region="us-east-1", offline=True)
        image = backend._training_image()
        assert "683313688378" in image

    def test_unknown_region_falls_back(self):
        backend = SageMakerBackend(bucket="b", role_arn="r", region="mars-east-1", offline=True)
        image = backend._training_image()
        # 預設 fallback
        assert "783357654285" in image


# ═══════════════════════════════════════════════════════════════════════════════
# ModelHubBackend — 基本驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelHubBackend:
    def test_offline_trigger(self):
        backend = ModelHubBackend(offline=True)
        job_id = backend.trigger_training("ETH", [{"x": 1}])
        assert "offline" in job_id
        assert "eth" in job_id

    def test_offline_poll(self):
        backend = ModelHubBackend(offline=True)
        result = backend.poll_result("test-job")
        assert result["status"] == "completed"

    def test_offline_download(self, tmp_path):
        backend = ModelHubBackend(offline=True)
        path = backend.download_artifact("test-job", tmp_path / "out")
        assert path.exists()

    def test_trigger_requires_req_no(self):
        backend = ModelHubBackend(offline=False)
        # 沒有 req_no 應 raise
        with pytest.raises(TrainingBackendConfigError, match="req_no"):
            backend.trigger_training("BTC", [{"x": 1}], config={})

    def test_trigger_with_req_no(self):
        """提供 req_no 時會嘗試呼叫 client（但因為 ModelHub 不可達會失敗）。"""
        backend = ModelHubBackend(offline=False)
        # 不實際連 ModelHub，mock client
        mock_client = MagicMock()
        backend._client = mock_client
        backend.trigger_training("BTC", [{"x": 1}], config={"req_no": "MH-001"})
        mock_client.trigger_retrain.assert_called_once()
