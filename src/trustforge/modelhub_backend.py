"""ModelHub 訓練後端——將既有 modelhub_client 包裝為 TrainingBackend Protocol。

這個 adapter 讓 ModelHub 能透過統一的 TrainingBackend 介面被使用，
與 SageMakerBackend 同等級並行。既有 modelhub_client.py 行為完全不動。

Ref: Issue #704
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .training_backend import TrainingBackendConfigError


class ModelHubBackend:
    """ModelHub 訓練後端 adapter。

    包裝 modelhub_client.py 的 trigger_retrain / poll_training_result / get_model_path，
    暴露 TrainingBackend Protocol 的統一介面。
    """

    backend_id: str = "modelhub"

    def __init__(self, *, offline: bool = False):
        self.offline = offline
        self._client = None

    def _get_client(self):
        """延遲建立 ModelHubClient，離線模式不建立。"""
        if self._client is None:
            if self.offline:
                raise TrainingBackendConfigError(
                    "ModelHubBackend: offline 模式不支援實際訓練操作"
                )
            from .modelhub_client import ModelHubClient
            self._client = ModelHubClient()
        return self._client

    def trigger_training(
        self, coin: str, rows: list[dict[str, Any]], *, config: dict[str, Any] | None = None
    ) -> str:
        """透過 ModelHub trigger_retrain 提交訓練。

        config 須含 "req_no"（ModelHub 必要的 request number）。
        """
        if self.offline:
            return f"modelhub-offline-{coin.lower()}"

        config = config or {}
        req_no = config.get("req_no")
        if not req_no:
            raise TrainingBackendConfigError(
                "ModelHubBackend.trigger_training: config 須含 'req_no'"
            )

        payload = {
            "coin": coin.upper(),
            "rows": rows,
            "row_count": len(rows),
        }
        self._get_client().trigger_retrain(req_no, payload)
        return req_no

    def poll_result(
        self, job_id: str, *, max_wait: float = 300.0, interval: float = 5.0
    ) -> dict[str, Any]:
        """輪詢 ModelHub training result。"""
        if self.offline:
            return {"status": "completed", "artifact_path": f"offline/{job_id}"}

        result = self._get_client().poll_training_result(
            job_id, max_wait=max_wait, interval=interval
        )
        status = str(result.get("status", "")).lower()
        terminal_success = {"completed", "complete", "succeeded", "success"}

        if status in terminal_success:
            return {
                "status": "completed",
                "artifact_path": result.get("artifact_sha256", ""),
                "raw": result,
            }
        else:
            return {
                "status": "failed",
                "failure_reason": result.get("error", status),
                "raw": result,
            }

    def download_artifact(self, job_id: str, local_path: Path) -> Path:
        """從 ModelHub 取得 model path（ModelHub 的 artifact 是路徑字串）。

        注意：ModelHub 的 get_model_path 回傳的是 untrusted 路徑字串，
        呼叫端必須另行驗證。這裡只負責取得路徑並寫 metadata 到 local_path。
        """
        if self.offline:
            # 離線模式產出佔位檔案
            local_path.mkdir(parents=True, exist_ok=True)
            placeholder = local_path / "model.json"
            placeholder.write_text(
                json.dumps({"points": [], "offline": True}), encoding="utf-8"
            )
            return placeholder

        model_path_str = self._get_client().get_model_path("trustforge", f"{job_id}-calibrator")
        # 寫 metadata（實際 artifact 取用由上層 submit 邏輯處理）
        local_path.mkdir(parents=True, exist_ok=True)
        meta = local_path / "modelhub_artifact_meta.json"
        meta.write_text(
            json.dumps({"model_path": model_path_str, "job_id": job_id}),
            encoding="utf-8",
        )
        return Path(model_path_str)
