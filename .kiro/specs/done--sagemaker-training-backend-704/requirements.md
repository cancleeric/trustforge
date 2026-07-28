# SageMaker 訓練後端擴充（#704）

## 訓練後端選擇

WHEN the environment variable `TRAINING_BACKEND` is set to `sagemaker`
THE SYSTEM SHALL use AWS SageMaker as the training backend for calibration models

WHEN the environment variable `TRAINING_BACKEND` is set to `modelhub` or is unset
THE SYSTEM SHALL use ModelHub as the training backend (current default behavior unchanged)

WHEN `TRAINING_BACKEND` is set to an unsupported value
THE SYSTEM SHALL raise a configuration error with a descriptive message

## 訓練資料上傳

WHEN a calibrator training run is triggered with SageMaker backend
THE SYSTEM SHALL upload the flat JSONL training data to S3 under a coin-specific prefix

WHEN `SAGEMAKER_TRAINING_BUCKET` is not set
THE SYSTEM SHALL raise a configuration error before attempting upload

WHEN S3 upload fails due to permission or network error
THE SYSTEM SHALL return structured status `unavailable` without leaking credentials or raw exceptions

## 訓練 Job 觸發

WHEN training data upload succeeds
THE SYSTEM SHALL create a SageMaker Training Job with the uploaded S3 URI as input channel

WHEN `SAGEMAKER_ROLE_ARN` is not set
THE SYSTEM SHALL raise a configuration error before attempting job creation

WHEN the SageMaker Training Job creation fails
THE SYSTEM SHALL return structured status `error` with fail-closed semantics

## 訓練 Job 輪詢

WHEN a SageMaker Training Job is running
THE SYSTEM SHALL poll `describe_training_job` at configurable intervals until terminal status

WHEN polling exceeds the configured maximum wait time (default 300s)
THE SYSTEM SHALL return structured status `timeout`

WHEN the ExecutionLog 15-minute budget is insufficient for continued polling
THE SYSTEM SHALL return structured status `timeout` and stop polling

WHEN the Training Job reaches status `Failed`
THE SYSTEM SHALL return structured status `error` with the failure reason

WHEN the Training Job reaches status `Completed`
THE SYSTEM SHALL proceed to artifact retrieval

## Artifact 取回與驗證

WHEN a Training Job completes successfully
THE SYSTEM SHALL download the model artifact from S3 ModelArtifacts path

WHEN the downloaded artifact is unpacked
THE SYSTEM SHALL verify it contains a valid `model.json` loadable by `load_calibration_model()`

WHEN the artifact SHA256 does not match the expected digest
THE SYSTEM SHALL return structured status `error` and not publish any proposal

WHEN the artifact is valid
THE SYSTEM SHALL produce an immutable proposal identical in structure to ModelHub proposals

## 治理約束

WHEN any SageMaker-backed proposal is produced
THE SYSTEM SHALL set `automatic_apply: false` and `requires_human_approval: true`

WHEN a SageMaker training run is invoked with `--dry-run`
THE SYSTEM SHALL execute all steps except actual AWS API calls, and produce only an execution log

WHEN offline mode is enabled
THE SYSTEM SHALL return placeholder results without making any AWS API calls

## Artifact 格式相容性

WHEN a SageMaker Training Job produces an artifact
THE SYSTEM SHALL ensure the artifact format is identical to ModelHub-produced artifacts

WHEN `load_calibration_model()` is called on a SageMaker-produced artifact
THE SYSTEM SHALL load successfully and return valid calibration points

## CLI 介面

WHEN the user runs `trustforge sagemaker-train --all`
THE SYSTEM SHALL trigger calibrator training for all five coins (BTC/ETH/SOL/BNB/XRP)

WHEN the user runs `trustforge sagemaker-train --coin BTC`
THE SYSTEM SHALL trigger calibrator training for the specified coin only

WHEN the user runs `trustforge sagemaker-train --all --dry-run`
THE SYSTEM SHALL simulate the full flow without AWS API calls and produce execution logs

## 不變行為（回歸防護）

WHEN `TRAINING_BACKEND=modelhub` or unset
THE SYSTEM SHALL CONTINUE TO use ModelHub via `modelhub_client.py` with no behavioral change

WHEN the pipeline runs in standard analysis mode
THE SYSTEM SHALL CONTINUE TO use local `apply_calibration()` for inference regardless of training backend

WHEN Bedrock is invoked for LLM tasks
THE SYSTEM SHALL CONTINUE TO route through `bedrock.py` with no change

WHEN calibration model inference is performed
THE SYSTEM SHALL CONTINUE TO use local pure-Python `apply_calibration()` (no remote endpoint)
