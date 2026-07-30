#!/usr/bin/env python3
"""Explicit, bounded AWS administration for preview admission."""

from __future__ import annotations

import argparse
import os


def _config():
    from trustforge.preview_admission_deployment import PreviewDeploymentConfig

    return PreviewDeploymentConfig.from_env(
        expected_kms_key_arn=os.environ["TRUSTFORGE_PREVIEW_TABLE_KMS_KEY_ARN"],
        expected_table_arn=os.environ["TRUSTFORGE_PREVIEW_TABLE_ARN"],
    )


def _integer(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdigit():
        raise ValueError("invalid preview admin configuration")
    return int(value)


def _lifecycle(config):
    from trustforge.preview_admission_executor import (
        AwsQuotaKeyReference,
        AwsQuotaLifecycleBootstrap,
    )
    from trustforge.preview_trusted_clock import TrustedUtcInterval

    previous = None
    previous_activated = superseded = retire_not_before = None
    activated = _integer("TRUSTFORGE_PREVIEW_QUOTA_KEY_ACTIVATED")
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
    return AwsQuotaLifecycleBootstrap(
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-aws", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    sub.add_parser("install-lifecycle")
    sub.add_parser("recover")
    sub.add_parser("disable-check")
    args = parser.parse_args()
    if not args.allow_aws:
        parser.error("--allow-aws is required")

    from trustforge.preview_admission_deployment import (
        PreviewAdmissionRuntimeComposer,
        bounded_admin_recover_and_disable_check,
        read_only_preview_probe,
    )

    config = _config()
    if args.command == "probe":
        result = read_only_preview_probe(
            config, region_name=os.environ.get("AWS_REGION")
        )
        print(f"preview_admission_admin={result.status.value}")
        return 0 if result.enabled else 1
    if args.command == "install-lifecycle":
        PreviewAdmissionRuntimeComposer.install_production_lifecycle(
            config=config,
            lifecycle=_lifecycle(config),
            region_name=os.environ.get("AWS_REGION"),
        )
        print("preview_admission_admin=lifecycle_attached")
        return 0
    composed = PreviewAdmissionRuntimeComposer.evaluate_production(
        config=config,
        lifecycle=_lifecycle(config),
        region_name=os.environ.get("AWS_REGION"),
    )
    if not composed.enabled:
        print("preview_admission_admin=unavailable")
        return 1
    runtime = composed.runtime()
    if args.command == "recover":
        interval = runtime.executor._durable_gate._trusted_clock.refresh()
        result = runtime.lease_recovery.run(interval)
        print(f"preview_admission_admin={result.outcome.value}")
        return 0 if result.outcome.value != "unavailable" else 1
    decision = bounded_admin_recover_and_disable_check(runtime)
    print(f"preview_admission_admin={decision.reason}")
    return 0 if decision.safe_to_disable else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("preview_admission_admin=failed")
        raise SystemExit(1) from None
