from __future__ import annotations

from copy import deepcopy
import importlib.util
import os

import pytest
import yaml

from trustforge.preview_admission_deployment import (
    DEFAULT_TABLE,
    FEATURE_ENV,
    KEY_PARAMETER_ENV,
    TABLE_ENV,
    PreviewAdmissionRuntimeComposer,
    PreviewAdmissionProductionRuntime,
    PreviewDeploymentConfig,
    PreviewDeploymentStatus,
    PreviewDisableDecision,
    PreviewDisableObservation,
    PreviewReleaseStage,
    advance_release_stage,
    evaluate_preview_disable,
)
from trustforge.preview_admission_executor import PreviewAdmissionExecutor
from trustforge.preview_admission_executor import (
    AwsQuotaKeyReference,
    AwsQuotaLifecycleBootstrap,
    MIN_OVERLAP_SECONDS,
)
from trustforge.preview_durable_admission_gate import DurableAdmissionGate
from trustforge.preview_lease_recovery import (
    PreviewAmbiguityRecovery,
    PreviewLeaseRecovery,
)
from trustforge.preview_terminal_reconcile import PreviewTerminalReconciler
from trustforge.preview_trusted_clock import TrustedUtcInterval


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
        quota_key_parameter="/trustforge/preview-admission/quota-hmac",
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
        KEY_PARAMETER_ENV: "/trustforge/preview-admission/quota-hmac",
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


def test_config_requires_exact_adjacent_previous_revision_and_incarnation():
    values = {
        FEATURE_ENV: "1",
        KEY_PARAMETER_ENV: "/trustforge/preview-admission/current",
        "TRUSTFORGE_PREVIEW_QUOTA_KEY_VERSION": "3",
        "TRUSTFORGE_PREVIEW_QUOTA_KEY_INCARNATION": "incarnation-3",
        "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_PARAMETER": "/trustforge/preview-admission/previous",
        "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_VERSION": "2",
        "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_INCARNATION": "incarnation-2",
    }
    config = PreviewDeploymentConfig.from_env(
        expected_kms_key_arn=KMS_ARN,
        expected_table_arn=TABLE_ARN,
        environ=values,
    )
    assert config.quota_key_version == 3
    assert config.previous_quota_key_version == 2
    with pytest.raises(ValueError, match="previous quota key"):
        PreviewDeploymentConfig.from_env(
            expected_kms_key_arn=KMS_ARN,
            expected_table_arn=TABLE_ARN,
            environ={
                **values,
                "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_INCARNATION":
                    "incarnation-3",
            },
        )


def test_production_runtime_seals_one_shared_client_graph():
    client = object()
    table = DEFAULT_TABLE
    clock = object()
    gate = object.__new__(DurableAdmissionGate)
    gate._client = client
    gate._table = table
    gate._trusted_clock = clock
    lifecycle = type("Lifecycle", (), {})()
    lifecycle._client = client
    lifecycle._clock = clock
    executor = object.__new__(PreviewAdmissionExecutor)
    executor._client = client
    executor._table_name = table
    executor._durable_gate = gate
    executor._lifecycle_authority = lifecycle
    terminal = object.__new__(PreviewTerminalReconciler)
    terminal._client = client
    terminal._table_name = table
    lease = object.__new__(PreviewLeaseRecovery)
    lease._client = client
    lease._terminal = terminal
    ambiguity = object.__new__(PreviewAmbiguityRecovery)
    ambiguity._client = client
    ambiguity._terminal = terminal
    ambiguity._gate = gate

    runtime = PreviewAdmissionProductionRuntime(
        executor, terminal, lease, ambiguity
    )
    assert runtime.executor._client is runtime.terminal._client

    ambiguity._client = object()
    with pytest.raises(ValueError, match="split preview authority"):
        PreviewAdmissionProductionRuntime(executor, terminal, lease, ambiguity)


def test_lifecycle_config_mismatch_returns_before_any_aws_io(monkeypatch):
    import boto3

    calls = []
    monkeypatch.setattr(
        boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    result = PreviewAdmissionRuntimeComposer.evaluate_production(
        config=_config(),
        lifecycle=AwsQuotaLifecycleBootstrap(
            generation=1,
            issued=TrustedUtcInterval(1, 1),
            activated=1,
            current=AwsQuotaKeyReference(
                "/trustforge/wrong", 1, "quota-1"
            ),
        ),
    )
    assert result.status is PreviewDeploymentStatus.UNAVAILABLE
    assert result.reason == "lifecycle_config_mismatch"
    assert calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda client: setattr(
            client,
            "ttl",
            {"TimeToLiveStatus": "DISABLED", "AttributeName": "ttl"},
        ),
        lambda client: client.table["SSEDescription"].update(
            KMSMasterKeyArn="arn:wrong"
        ),
        lambda client: setattr(client, "tags", []),
    ],
)
def test_install_lifecycle_preflights_infra_before_ssm_or_writes(
    monkeypatch, mutate
):
    import boto3

    client = Client()
    mutate(client)
    aws_calls = []
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service_name, **kwargs: (
            aws_calls.append((service_name, kwargs)) or client
        ),
    )
    composed = []
    monkeypatch.setattr(
        PreviewAdmissionProductionRuntime,
        "from_aws_components",
        lambda *args, **kwargs: composed.append((args, kwargs)),
    )
    lifecycle = AwsQuotaLifecycleBootstrap(
        generation=1,
        issued=TrustedUtcInterval(1, 1),
        activated=1,
        current=AwsQuotaKeyReference(
            "/trustforge/preview-admission/quota-hmac", 1, "quota-1"
        ),
    )
    with pytest.raises(ValueError):
        PreviewAdmissionRuntimeComposer.install_production_lifecycle(
            config=_config(), lifecycle=lifecycle
        )
    assert [name for name, _ in aws_calls] == ["dynamodb"]
    assert composed == []


def test_exact_binding_supports_later_single_generations():
    import trustforge.preview_admission_deployment as deployment

    for generation in (1, 3, 4, 9):
        lifecycle = AwsQuotaLifecycleBootstrap(
            generation=generation,
            issued=TrustedUtcInterval(1, 1),
            activated=1,
            current=AwsQuotaKeyReference(
                "/trustforge/preview-admission/quota-hmac", 1, "quota-1"
            ),
        )
        assert deployment._lifecycle_matches_config(lifecycle, _config())

    overlap_config = PreviewDeploymentConfig(
        requested=True,
        table_name=DEFAULT_TABLE,
        quota_key_parameter="/trustforge/preview-admission/current",
        expected_kms_key_arn=KMS_ARN,
        expected_table_arn=TABLE_ARN,
        quota_key_version=2,
        quota_key_incarnation="incarnation-2",
        previous_quota_key_parameter="/trustforge/preview-admission/previous",
        previous_quota_key_version=1,
        previous_quota_key_incarnation="incarnation-1",
    )
    for generation in (2, 5, 8):
        overlap = AwsQuotaLifecycleBootstrap(
            generation=generation,
            issued=TrustedUtcInterval(2, 2),
            activated=2,
            current=AwsQuotaKeyReference(
                "/trustforge/preview-admission/current", 2, "incarnation-2"
            ),
            previous=AwsQuotaKeyReference(
                "/trustforge/preview-admission/previous", 1, "incarnation-1"
            ),
            previous_activated=1,
            superseded=2,
            retire_not_before=2 + MIN_OVERLAP_SECONDS,
        )
        assert deployment._lifecycle_matches_config(overlap, overlap_config)


def test_bootstrap_is_conditional_idempotent_and_never_contains_secret_bytes():
    spec = importlib.util.spec_from_file_location(
        "preview_bootstrap", "deploy/bootstrap_preview_admission_store.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class BootstrapClient:
        def __init__(self):
            self.calls = []

        def put_item(self, **kwargs):
            self.calls.append(kwargs)

    client = BootstrapClient()
    module.bootstrap(client, DEFAULT_TABLE, 123)
    assert len(client.calls) == 2
    assert all(
        call["ConditionExpression"]
        == "attribute_not_exists(pk) AND attribute_not_exists(sk)"
        for call in client.calls
    )
    assert b"secret" not in repr(client.calls).encode()
    with pytest.raises(ValueError, match="invalid bootstrap target"):
        module.bootstrap(client, DEFAULT_TABLE, module.MAX_EPOCH_MINUTE + 1)


def test_bootstrap_collision_requires_exact_existing_row():
    spec = importlib.util.spec_from_file_location(
        "preview_bootstrap_collision",
        "deploy/bootstrap_preview_admission_store.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class Collision(Exception):
        response = {"Error": {"Code": "ConditionalCheckFailedException"}}

    class ClientWithCollision:
        def __init__(self, existing):
            self.existing = existing

        def put_item(self, **kwargs):
            raise Collision()

        def get_item(self, **kwargs):
            return {"Item": self.existing}

    item = {"pk": {"S": "x"}, "sk": {"S": "y"}}
    module._put_if_absent(ClientWithCollision(item), DEFAULT_TABLE, item)
    with pytest.raises(RuntimeError, match="does not match"):
        module._put_if_absent(
            ClientWithCollision({"pk": {"S": "other"}}), DEFAULT_TABLE, item
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
    assert (
        advance_release_stage(
            enabled,
            PreviewReleaseStage.DISABLED,
            readiness=ready,
            canary_verified=True,
            disable_decision=PreviewDisableDecision(False, "pending"),
        )
        is PreviewReleaseStage.DISABLED
    )


def test_cloudformation_is_default_off_retained_and_least_privilege():
    class CloudFormationLoader(yaml.SafeLoader):
        pass

    def construct_tag(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node)
        else:
            value = loader.construct_mapping(node)
        return {suffix: value}

    CloudFormationLoader.add_multi_constructor("!", construct_tag)
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
    assert "CurrentQuotaKeyParameterArn" in text
    assert "PreviousQuotaKeyParameterArn" in text
    assert "kms:EncryptionContext:PARAMETER_ARN" in text


def test_zero_downtime_canary_inherits_exact_default_off_flag():
    text = open("deploy/zero_downtime_restart.sh", encoding="utf-8").read()
    assert "TRUSTFORGE_PREVIEW_ADMISSION_ENABLED:-0" in text
    assert (
        "Environment=TRUSTFORGE_PREVIEW_ADMISSION_ENABLED="
        "$PREVIEW_ADMISSION_ENABLED"
    ) in text
    assert '!= "0"' in text and '!= "1"' in text


def test_off_smoke_needs_no_other_environment_or_aws_import(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "preview_smoke", "deploy/preview_admission_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    for key in tuple(os.environ):
        if key.startswith("TRUSTFORGE_PREVIEW_"):
            monkeypatch.delenv(key, raising=False)
    assert module.main() == 0
    assert capsys.readouterr().out.strip() == "preview_admission_smoke=off"


def test_production_deploy_and_admin_have_strict_preview_controls():
    deploy = open("deploy/deploy_ec2.sh", encoding="utf-8").read()
    admin = open("deploy/preview_admission_admin.py", encoding="utf-8").read()
    bootstrap = open(
        "deploy/bootstrap_preview_admission_store.py", encoding="utf-8"
    ).read()
    assert "TRUSTFORGE_PREVIEW_ADMISSION_ENABLED:-0" in deploy
    assert "PREVIEW_ENV_KEYS" in deploy
    assert "--allow-aws" in admin and "disable-check" in admin
    assert "--allow-aws" in bootstrap
    assert "MAX_EPOCH_MINUTE" in bootstrap
    assert "TableKmsKeyArn" in open(
        "deploy/preview-admission-store.yaml", encoding="utf-8"
    ).read()


def test_web_preview_startup_is_default_off_and_fail_isolated(monkeypatch):
    import boto3
    import trustforge.web as web

    calls = []
    monkeypatch.delenv("TRUSTFORGE_PREVIEW_ADMISSION_ENABLED", raising=False)
    monkeypatch.setattr(
        boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    web._initialize_preview_admission()
    assert web._PREVIEW_ADMISSION_STATUS == "disabled"
    assert web._PREVIEW_ADMISSION_RUNTIME is None
    assert calls == []

    monkeypatch.setenv("TRUSTFORGE_PREVIEW_ADMISSION_ENABLED", "1")
    import trustforge.preview_admission_deployment as deployment

    monkeypatch.setattr(
        deployment,
        "initialize_preview_runtime_from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("provider detail")),
    )
    web._initialize_preview_admission()
    assert web._PREVIEW_ADMISSION_STATUS == "unavailable"
    assert web._PREVIEW_ADMISSION_RUNTIME is None
