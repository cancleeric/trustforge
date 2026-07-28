from pathlib import Path


def test_existing_instance_reconciles_explicit_model_before_activation():
    script = (
        Path(__file__).resolve().parent.parent / "deploy" / "activate_release.sh"
    ).read_text(encoding="utf-8")
    mutation = "MODEL_RECONCILE_COMMAND"
    restart = "bash deploy/zero_downtime_restart.sh"
    assert mutation in script
    assert script.index(mutation, script.index("# Step 5")) < script.index(
        restart, script.index("# Step 5")
    )
    assert ".activation-trustforge.service.bak" in script
    rollback = script[script.index("ROLLBACK()"):script.index("# ---- lock helpers")]
    assert "cp -p /opt/trustforge/.activation-trustforge.service.bak" in rollback
    assert 'if [ "$UNIT_BACKUP_CAPTURED" -eq 1 ]' in rollback
    assert '"systemctl restart trustforge"' in rollback
    assert rollback.index("service configuration") < rollback.index(
        'if [ -n "$ACTIVE_DIGEST" ]'
    )


def test_empty_model_preserves_existing_instance_setting():
    script = (
        Path(__file__).resolve().parent.parent / "deploy" / "activate_release.sh"
    ).read_text(encoding="utf-8")
    assert 'MODEL_RECONCILE_COMMAND="true"' in script


def test_zero_downtime_canary_reads_reconciled_primary_model():
    script = (
        Path(__file__).resolve().parent.parent
        / "deploy"
        / "zero_downtime_restart.sh"
    ).read_text(encoding="utf-8")
    assert "s/^Environment=BEDROCK_MODEL_ID=//p" in script
    assert 'Environment=BEDROCK_MODEL_ID=${MODEL_ID}' in script
