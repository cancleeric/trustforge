"""訓練後端抽象層——ModelHub 和 SageMaker 的統一 Protocol。

設計原則：
  - TrainingBackend Protocol 定義訓練後端的最小介面
  - connector composition root 負責根據環境變數選擇實作
  - ModelHub 和 SageMaker 是同等級訓練平台（都有 GPU、能跑 fit、產 artifact）
  - 推論走本地 apply_calibration()，訓練後端只管 fit + 產 artifact

Ref: Issue #704, Spec .kiro/specs/sagemaker-training-backend-704/
"""
from .training_backend_contracts import TrainingBackend, TrainingBackendConfigError
from .training_backend_resolver import resolve_training_backend

__all__ = ["TrainingBackend", "TrainingBackendConfigError", "resolve_training_backend"]
