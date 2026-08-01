"""AWS SageMaker 訓練後端——與 ModelHub 同等級的訓練平台。

職責：上傳訓練資料到 S3 → 觸發 Training Job → 輪詢完成 → 取回 artifact。
不做推論、不做 endpoint 部署。校準模型推論走本地 apply_calibration()。

設計決定：
  - boto3 延遲匯入：離線模式不需 AWS 憑證
  - 兩個 client：sagemaker（create/describe job）+ s3（upload/download）
  - Config: connect_timeout=10s, read_timeout=30s, total_max_attempts=2
  - artifact 格式與 ModelHub 一致：model.json（isotonic 映射表）

Ref: Issue #704, #707
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .training_backend import TrainingBackendConfigError

# 預設環境變數
_DEFAULT_REGION = "us-east-1"
_DEFAULT_INSTANCE_TYPE = "ml.m5.large"
_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_MAX_WAIT = 300.0

# Training Job terminal 狀態
_TERMINAL_STATUSES = frozenset({"Completed", "Failed", "Stopped"})


class SageMakerBackend:
    """AWS SageMaker 訓練後端。

    與 ModelHub 同等級——都有 GPU、都能跑 training job、都產 artifact。
    差別只在基礎設施：一個是自建的，一個是 AWS managed service。
    """

    backend_id: str = "sagemaker"

    def __init__(
        self,
        *,
        bucket: str | None = None,
        role_arn: str | None = None,
        region: str | None = None,
        instance_type: str | None = None,
        use_spot: bool | None = None,
        offline: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.offline = offline
        self._sleep = sleep
        self._monotonic = monotonic

        # 從環境變數或參數讀取設定
        self.bucket = bucket or os.getenv("SAGEMAKER_TRAINING_BUCKET", "")
        self.role_arn = role_arn or os.getenv("SAGEMAKER_ROLE_ARN", "")
        self.region = region or os.getenv("AWS_REGION", _DEFAULT_REGION)
        self.instance_type = instance_type or os.getenv(
            "SAGEMAKER_INSTANCE_TYPE", _DEFAULT_INSTANCE_TYPE
        )
        self.use_spot = (
            use_spot if use_spot is not None
            else os.getenv("SAGEMAKER_USE_SPOT", "false").lower() in ("true", "1", "yes")
        )

        # 延遲初始化的 boto3 clients
        self._sm_client = None
        self._s3_client = None

    def _validate_config(self) -> None:
        """驗證必要設定，缺少則 raise。"""
        if not self.bucket:
            raise TrainingBackendConfigError(
                "SAGEMAKER_TRAINING_BUCKET 未設定。"
                "請設定 S3 bucket 名稱供訓練資料上傳。"
            )
        if not self.role_arn:
            raise TrainingBackendConfigError(
                "SAGEMAKER_ROLE_ARN 未設定。"
                "請設定 SageMaker execution role ARN。"
            )

    def _sagemaker(self):
        """延遲建立 SageMaker client。"""
        if self._sm_client is None:
            import boto3
            from botocore.config import Config

            self._sm_client = boto3.client(
                "sagemaker",
                region_name=self.region,
                config=Config(
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"total_max_attempts": 2},
                ),
            )
        return self._sm_client

    def _s3(self):
        """延遲建立 S3 client。"""
        if self._s3_client is None:
            import boto3
            from botocore.config import Config

            self._s3_client = boto3.client(
                "s3",
                region_name=self.region,
                config=Config(
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"total_max_attempts": 2},
                ),
            )
        return self._s3_client

    def _make_job_name(self, coin: str) -> str:
        """產生 Training Job 名稱：trustforge-calibrator-{coin}-{timestamp}。"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"trustforge-calibrator-{coin.lower()}-{ts}"

    def _s3_prefix(self, coin: str, timestamp: str) -> str:
        """S3 key prefix for a training run."""
        return f"trustforge/training/{coin.upper()}/{timestamp}"

    def _upload_training_data(self, coin: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
        """上傳 JSONL 訓練資料到 S3。回傳 (s3_uri, timestamp)。"""
        self._validate_config()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        prefix = self._s3_prefix(coin, ts)
        key = f"{prefix}/input/data.jsonl"

        # 組裝 JSONL
        lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
        body = "\n".join(lines).encode("utf-8")

        self._s3().put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/x-ndjson",
        )

        s3_uri = f"s3://{self.bucket}/{prefix}/input"
        return s3_uri, ts

    def trigger_training(
        self, coin: str, rows: list[dict[str, Any]], *, config: dict[str, Any] | None = None
    ) -> str:
        """上傳資料 + 建立 SageMaker Training Job。回傳 job_name。"""
        if self.offline:
            return f"sagemaker-offline-{coin.lower()}"

        self._validate_config()
        s3_uri, ts = self._upload_training_data(coin, rows)
        job_name = self._make_job_name(coin)

        # Training Job 設定
        training_params: dict[str, Any] = {
            "TrainingJobName": job_name,
            "RoleArn": self.role_arn,
            "AlgorithmSpecification": {
                "TrainingImage": self._training_image(),
                "TrainingInputMode": "File",
            },
            "InputDataConfig": [
                {
                    "ChannelName": "training",
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": s3_uri,
                            "S3DataDistributionType": "FullyReplicated",
                        }
                    },
                    "ContentType": "application/x-ndjson",
                }
            ],
            "OutputDataConfig": {
                "S3OutputPath": f"s3://{self.bucket}/trustforge/training/{coin.upper()}/{ts}/output",
            },
            "ResourceConfig": {
                "InstanceType": self.instance_type,
                "InstanceCount": 1,
                "VolumeSizeInGB": 5,
            },
            "StoppingCondition": {
                "MaxRuntimeInSeconds": 600,  # 10 分鐘硬上限（校準模型極小）
            },
            "HyperParameters": {
                "coin": coin.upper(),
            },
        }

        # Spot instance（省錢）
        if self.use_spot:
            training_params["EnableManagedSpotTraining"] = True
            training_params["StoppingCondition"]["MaxWaitTimeInSeconds"] = 900

        self._sagemaker().create_training_job(**training_params)
        return job_name

    def poll_result(
        self, job_id: str, *, max_wait: float = _DEFAULT_POLL_MAX_WAIT, interval: float = _DEFAULT_POLL_INTERVAL
    ) -> dict[str, Any]:
        """輪詢 Training Job 至 terminal 狀態。"""
        if self.offline:
            return {"status": "completed", "artifact_path": f"s3://offline/{job_id}/output"}

        deadline = self._monotonic() + max_wait

        while True:
            resp = self._sagemaker().describe_training_job(TrainingJobName=job_id)
            status = resp.get("TrainingJobStatus", "Unknown")

            if status in _TERMINAL_STATUSES:
                if status == "Completed":
                    artifact_path = (
                        resp.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
                    )
                    return {
                        "status": "completed",
                        "artifact_path": artifact_path,
                        "raw": resp,
                    }
                else:
                    reason = resp.get("FailureReason", status)
                    return {
                        "status": "failed",
                        "failure_reason": reason,
                        "raw": resp,
                    }

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return {
                    "status": "timeout",
                    "failure_reason": f"Poll timeout after {max_wait}s, last status: {status}",
                }

            self._sleep(min(interval, remaining))

    def download_artifact(self, job_id: str, local_path: Path) -> Path:
        """從 S3 下載 model artifact（model.tar.gz）並解壓。回傳 model.json 的 Path。"""
        if self.offline:
            local_path.mkdir(parents=True, exist_ok=True)
            placeholder = local_path / "model.json"
            placeholder.write_text(
                json.dumps({"points": [], "offline": True, "job_id": job_id}),
                encoding="utf-8",
            )
            return placeholder

        # 先取得 artifact S3 path
        resp = self._sagemaker().describe_training_job(TrainingJobName=job_id)
        s3_model_uri = resp.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
        if not s3_model_uri:
            raise TrainingBackendConfigError(
                f"Training job {job_id} 沒有 ModelArtifacts S3 路徑"
            )

        # 解析 S3 URI
        # s3://bucket/key/model.tar.gz
        if not s3_model_uri.startswith("s3://"):
            raise TrainingBackendConfigError(f"Invalid S3 URI: {s3_model_uri}")
        parts = s3_model_uri[5:].split("/", 1)
        if len(parts) != 2:
            raise TrainingBackendConfigError(f"Invalid S3 URI: {s3_model_uri}")
        bucket, key = parts

        # 下載
        response = self._s3().get_object(Bucket=bucket, Key=key)
        tar_bytes = response["Body"].read()

        # 解壓 model.tar.gz
        local_path.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            # 安全解壓：只解 model.json，防止 path traversal
            for member in tar.getmembers():
                if member.name in ("model.json", "./model.json"):
                    member.name = "model.json"  # 正規化路徑
                    tar.extract(member, path=local_path)
                    break
            else:
                raise TrainingBackendConfigError(
                    f"Artifact {s3_model_uri} 不含 model.json"
                )

        model_path = local_path / "model.json"
        if not model_path.is_file():
            raise TrainingBackendConfigError(
                f"解壓後 model.json 不存在於 {local_path}"
            )

        return model_path

    def _training_image(self) -> str:
        """回傳 SageMaker Training container image URI。

        使用 AWS 提供的 SKLearn 預置容器（只借 Python runtime，
        實際訓練用純 Python PAV 演算法，不用 sklearn library）。
        """
        # SKLearn container image URI 格式：
        # {account}.dkr.ecr.{region}.amazonaws.com/sagemaker-scikit-learn:{version}-cpu-py3
        account_map: dict[str, str] = {
            "ap-southeast-2": "783357654285",
            "us-east-1": "683313688378",
            "us-west-2": "246618743249",
            "eu-west-1": "141502667606",
        }
        account = account_map.get(self.region, "783357654285")
        return (
            f"{account}.dkr.ecr.{self.region}.amazonaws.com"
            f"/sagemaker-scikit-learn:1.2-1-cpu-py3"
        )
