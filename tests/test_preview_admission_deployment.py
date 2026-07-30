from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from trustforge.preview_admission_deployment import (
    DEFAULT_TABLE,
    FEATURE_ENV,
    KEY_PARAMETER_ENV,
    TABLE_ENV,
    PreviewAdmissionRuntimeComposer,
    PreviewDeploymentConfig,
    PreviewDeploymentStatus,
    PreviewDisableDecision,
    PreviewDisableObservation,
    PreviewReleaseStage,
    advance_release_stage,
    evaluate_preview_disable,
)


TABLE_ARN = (
    "arn:aws:dynamodb:ap-southeast-2:123456789012:"
    "table/trustforge-preview-admission"
)
KMS_ARN = "arn:aws:kms:ap-southeast-2:123456789012:key/key-1"


def _ok(payload):
    return {
        **payload,
        "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "request-1"},
    }


class Client:
    def __init__(self):
        self.calls = []
        self.table = {
            "TableName": DEFAULT_TABLE,
            "TableArn": TABLE_ARN,
            "TableStatus": "ACTIVE",
            "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            "SSEDescription": {
                "Status": "ENABLED",
                "SSEType": "KMS",
                "KMSMasterKeyArn": KMS_ARN,
            },
        }
        self.ttl = {"TimeToLiveStatus": "ENABLED", "AttributeName": "ttl"}
        self.backups = {
            "ContinuousBackupsStatus": "ENABLED",
            "PointInTimeRecoveryDescription": {
                "PointInTimeRecoveryStatus": "ENABLED"
            },
        }
        self.tags = [
            {"Key": "TrustForgeComponent", "Value": "preview-admission"}
        ]

    def describe_table(self, **kwargs):
        self.calls.append(("table", kwargs))
        return _ok({"Table": deepcopy(self.table)})

    def describe_time_to_live(self, **kwargs):
        self.calls.append(("ttl", kwargs))
        return _ok({"TimeToLiveDescription": deepcopy(self.ttl)})

    def describe_continuous_backups(self, **kwargs):
        self.calls.append(("pitr", kwargs))
        return _ok({"ContinuousBackupsDescription": deepcopy(self.backups)})

    def list_tags_of_resource(self, **kwargs):
        self.calls.append(("tags", kwargs))
        return _ok({"Tags": deepcopy(self.tags)})


def _config(requested=True):
    return PreviewDeploymentConfig(
        requested=requested,
        table_name=DEFAULT_TABLE,
        quota_key_parameter="/trustforge/runtime/preview/quota-hmac",
        expected_kms_key_arn=KMS_ARN,
        expected_table_arn=TABLE_ARN,
    )


def test_default_off_performs_zero_aws_or_composition_io():
    client = Client()
    composed = []
    result = PreviewAdmissionRuntimeComposer(
        client=client,
        config=_config(False),
        compose=lambda: composed.append(True),
    ).evaluate()

    assert result.status is PreviewDeploymentStatus.DISABLED
    assert result.enabled is False
    assert client.calls == []
    assert composed == []
    with pytest.raises(ValueError):
        result.runtime()


def test_env_flag_is_exact_default_off_and_has_no_fallback():
    base = {
        TABLE_ENV: DEFAULT_TABLE,
        KEY_PARAMETER_ENV: "/trustforge/runtime/preview/quota-hmac",
    }
    disabled = PreviewDeploymentConfig.from_env(
        expected_kms_key_arn=KMS_ARN,
        expected_table_arn=TABLE_ARN,
        environ=base,
    )
    enabled = PreviewDeploymentConfig.from_env(
        expected_kms_key_arn=KMS_ARN,
        expected_table_arn=TABLE_ARN,
        environ={**base, FEATURE_ENV: "1"},
    )
    assert disabled.requested is False
    assert enabled.requested is True
    for invalid in ("true", "yes", "2", ""):
        with pytest.raises(ValueError):
            PreviewDeploymentConfig.from_env(
                expected_kms_key_arn=KMS_ARN,
                expected_table_arn=TABLE_ARN,
                environ={**base, FEATURE_ENV: invalid},
            )


def test_ready_means_verified_store_and_composed_runtime():
    client = Client()
    runtime = object()
    result = PreviewAdmissionRuntimeComposer(
        client=client, config=_config(), compose=lambda: runtime
    ).evaluate()

    assert result.status is PreviewDeploymentStatus.READY
    assert result.enabled is True
    assert result.runtime() is runtime
    assert result.checks == ("table", "kms", "ttl", "pitr", "dedicated")
    assert [name for name, _ in client.calls] == [
        "table",
        "ttl",
        "pitr",
        "tags",
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.table.update(TableStatus="CREATING"),
        lambda c: c.table["BillingModeSummary"].update(BillingMode="PROVISIONED"),
        lambda c: c.table["SSEDescription"].update(KMSMasterKeyArn="arn:wrong"),
        lambda c: setattr(c, "ttl", {"TimeToLiveStatus": "DISABLED"}),
        lambda c: c.backups["PointInTimeRecoveryDescription"].update(
            PointInTimeRecoveryStatus="DISABLED"
        ),
        lambda c: setattr(c, "tags", []),
    ],
)
def test_missing_or_mismatched_store_evidence_fails_closed(mutate):
    client = Client()
    mutate(client)
    composed = []
    result = PreviewAdmissionRuntimeComposer(
        client=client,
        config=_config(),
        compose=lambda: composed.append(True),
    ).evaluate()
    assert result.status is PreviewDeploymentStatus.UNAVAILABLE
    assert result.enabled is False
    assert composed == []


def test_malformed_aws_success_and_failed_composition_are_unavailable():
    client = Client()
    client.describe_table = lambda **kwargs: {"Table": client.table}
    assert (
        PreviewAdmissionRuntimeComposer(
            client=client, config=_config(), compose=object
        ).evaluate().status
        is PreviewDeploymentStatus.UNAVAILABLE
    )
    client = Client()
    assert (
        PreviewAdmissionRuntimeComposer(
            client=client, config=_config(), compose=lambda: None
        ).evaluate().status
        is PreviewDeploymentStatus.UNAVAILABLE
    )


@pytest.mark.parametrize(
    ("observation", "safe", "reason"),
    [
        (
            PreviewDisableObservation("open", False, 11, 10, "single"),
            True,
            "disable_safe_retain_state",
        ),
        (
            PreviewDisableObservation("dispatching", True, 11, 10, "single"),
            False,
            "pending_admission",
        ),
        (
            PreviewDisableObservation("open", False, 10, 10, "single"),
            False,
            "recovery_not_converged",
        ),
        (
            PreviewDisableObservation("open", False, 11, 10, "overlap"),
            False,
            "rotation_overlap_active",
        ),
    ],
)
def test_disable_check_is_conservative(observation, safe, reason):
    decision = evaluate_preview_disable(observation)
    assert decision == PreviewDisableDecision(safe, reason)


def test_release_state_machine_requires_readiness_canary_and_safe_rollback():
    ready = PreviewAdmissionRuntimeComposer(
        client=Client(), config=_config(), compose=object
    ).evaluate()
    canary = advance_release_stage(
        PreviewReleaseStage.DARK,
        PreviewReleaseStage.CANARY,
        readiness=ready,
        canary_verified=False,
    )
    enabled = advance_release_stage(
        canary,
        PreviewReleaseStage.ENABLED,
        readiness=ready,
        canary_verified=True,
    )
    assert (
        advance_release_stage(
            enabled,
            PreviewReleaseStage.DISABLED,
            readiness=ready,
            canary_verified=True,
            disable_decision=PreviewDisableDecision(
                True, "disable_safe_retain_state"
            ),
        )
        is PreviewReleaseStage.DISABLED
    )
    with pytest.raises(ValueError):
        advance_release_stage(
            PreviewReleaseStage.DARK,
            PreviewReleaseStage.CANARY,
            readiness=PreviewAdmissionRuntimeComposer(
                client=Client(), config=_config(False), compose=object
            ).evaluate(),
            canary_verified=False,
        )
    with pytest.raises(ValueError):
        advance_release_stage(
            enabled,
            PreviewReleaseStage.DISABLED,
            readiness=ready,
            canary_verified=True,
            disable_decision=PreviewDisableDecision(False, "pending"),
        )


def test_cloudformation_is_default_off_retained_and_least_privilege():
    class CloudFormationLoader(yaml.SafeLoader):
        pass

    CloudFormationLoader.add_multi_constructor(
        "!",
        lambda loader, suffix, node: {
            suffix: loader.construct_scalar(node)
            if isinstance(node, yaml.ScalarNode)
            else loader.construct_object(node)
        },
    )
    with open("deploy/preview-admission-store.yaml", encoding="utf-8") as source:
        template = yaml.load(source, Loader=CloudFormationLoader)
    text = open(
        "deploy/preview-admission-store.yaml", encoding="utf-8"
    ).read()
    table = template["Resources"]["PreviewAdmissionTable"]
    statements = template["Resources"]["PreviewAdmissionRuntimePolicy"][
        "Properties"
    ]["PolicyDocument"]["Statement"]

    assert template["Parameters"]["PreviewEnabled"]["Default"] == "0"
    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"
    assert table["Properties"]["TimeToLiveSpecification"] == {
        "AttributeName": "ttl",
        "Enabled": True,
    }
    assert table["Properties"]["PointInTimeRecoverySpecification"][
        "PointInTimeRecoveryEnabled"
    ] is True
    actions = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }
    assert "dynamodb:Scan" not in actions
    assert "ssm:GetParametersByPath" not in actions
    assert "ssm:GetParameter" in actions
    assert "kms:Decrypt" in actions
    assert 'Resource: "*"' not in text
    assert "AWS::DynamoDB::Table" in text
