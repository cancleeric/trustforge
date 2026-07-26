"""訓練後端抽象層——ModelHub 和 SageMaker 的統一 Protocol。

設計原則：
  - TrainingBackend Protocol 定義訓練後端的最小介面
  - resolve_training_backend() 根據環境變數選擇實作
  - ModelHub 和 SageMaker 是同等級訓練平台（都有 GPU、能跑 fit、產 artifact）
  - 推論走本地 apply_calibration()，訓練後端只管 fit + 產 artifact

Ref: Issue #704, Spec .kiro/specs/sagemaker-training-backend-704/
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TrainingBackend(Protocol):
    """訓練後端抽象——ModelHub 和 SageMaker 的統一介面。

    職責：接收訓練資料 → 觸發 training job → 輪詢完成 → 取回 artifact。
    不做推論、不做 endpoint 部署。
    """

    backend_id: str

    def trigger_training(
        self, coin: str, rows: list[dict[str, Any]], *, config: dict[str, Any] | None = None
    ) -> str:
        """提交訓練。回傳 job identifier（req_no 或 job_name）。"""
        ...

    def poll_result(
        self, job_id: str, *, max_wait: float = 300.0, interval: float = 5.0
    ) -> dict[str, Any]:
        """輪詢至 terminal 狀態。

        回傳 dict 至少含：
          - "status": "completed" | "failed" | "timeout"
          - "artifact_path": str (成功時)
          - "failure_reason": str (失敗時)
        """
        ...

    def download_artifact(self, job_id: str, local_path: Path) -> Path:
        """取回 model artifact 到本地路徑。回傳實際 model.json 的 Path。"""
        ...


class TrainingBackendConfigError(RuntimeError):
    """訓練後端設定錯誤（缺少必要環境變數等）。"""


def resolve_training_backend(*, offline: bool = False) -> TrainingBackend:
    """根據 TRAINING_BACKEND 環境變數選擇後端。

    - "modelhub" (預設): 使用既有 ModelHub loopback client
    - "sagemaker": 使用 AWS SageMaker Training Job
    - 其他值: raise TrainingBackendConfigError
    """
    backend = os.getenv("TRAINING_BACKEND", "modelhub").lower().strip()

    if backend == "sagemaker":
        from .sagemaker_client import SageMakerBackend
        return SageMakerBackend(offline=offline)
    elif backend == "modelhub":
        from .modelhub_backend import ModelHubBackend
        return ModelHubBackend(offline=offline)
    else:
        raise TrainingBackendConfigError(
            f"Unsupported TRAINING_BACKEND={backend!r}. "
            f"Allowed values: 'modelhub', 'sagemaker'"
        )
