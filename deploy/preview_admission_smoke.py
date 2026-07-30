#!/usr/bin/env python3
"""Fail-closed off/dark smoke for the preview admission production graph."""

from __future__ import annotations

import os

from trustforge.preview_admission_deployment import (
    PreviewAdmissionRuntimeComposer,
    PreviewDeploymentConfig,
)
from trustforge.preview_admission_executor import (
    AwsQuotaKeyReference,
    AwsQuotaLifecycleBootstrap,
)
from trustforge.preview_trusted_clock import TrustedUtcInterval


def _integer(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{name} must be an integer")
    return int(value)


def main() -> int:
    config = PreviewDeploymentConfig.from_env(
        expected_kms_key_arn=os.environ["TRUSTFORGE_PREVIEW_KMS_KEY_ARN"],
        expected_table_arn=os.environ["TRUSTFORGE_PREVIEW_TABLE_ARN"],
    )
    if not config.requested:
        print("preview_admission_smoke=off")
        return 0
    activated = _integer("TRUSTFORGE_PREVIEW_QUOTA_KEY_ACTIVATED")
    previous = None
    previous_activated = superseded = retire_not_before = None
    if config.previous_quota_key_parameter is not None:
        previous = AwsQuotaKeyReference(
            config.previous_quota_key_parameter,
            config.previous_quota_key_version,
            config.previous_quota_key_incarnation,
        )
        previous_activated = _integer(
            "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_ACTIVATED"
        )
        superseded = activated
        retire_not_before = _integer(
            "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_RETIRE_NOT_BEFORE"
        )
    lifecycle = AwsQuotaLifecycleBootstrap(
        generation=_integer("TRUSTFORGE_PREVIEW_QUOTA_LIFECYCLE_GENERATION"),
        issued=TrustedUtcInterval(
            _integer("TRUSTFORGE_PREVIEW_QUOTA_ISSUED_EARLIEST"),
            _integer("TRUSTFORGE_PREVIEW_QUOTA_ISSUED_LATEST"),
        ),
        activated=activated,
        current=AwsQuotaKeyReference(
            config.quota_key_parameter,
            config.quota_key_version,
            config.quota_key_incarnation,
        ),
        previous=previous,
        previous_activated=previous_activated,
        superseded=superseded,
        retire_not_before=retire_not_before,
    )
    result = PreviewAdmissionRuntimeComposer.evaluate_production(
        config=config,
        lifecycle=lifecycle,
        region_name=os.environ.get("AWS_REGION"),
    )
    print(f"preview_admission_smoke={result.status.value}")
    return 0 if result.enabled else 1


if __name__ == "__main__":
    raise SystemExit(main())
