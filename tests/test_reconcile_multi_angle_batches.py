from __future__ import annotations

import json
from argparse import Namespace

from scripts import reconcile_multi_angle_batches as cli


class _Store:
    def __init__(self, report=None, error=None):
        self.report = report or {
            "dry_run": True, "ready": [], "settled": [],
            "uncertain": [], "pending": [],
        }
        self.error = error
        self.calls = []

    def reconcile_stale_batches(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.report


def test_cli_defaults_to_dry_run_and_emits_structured_json(
    monkeypatch, tmp_path, capsys
):
    database = tmp_path / "atomic.db"
    database.touch()
    store = _Store()
    monkeypatch.setattr(
        cli, "SQLiteAtomicMultiAngleBatchStore", lambda _path: store
    )
    assert cli.main(["--sqlite", str(database)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["mode"] == "dry-run"
    assert store.calls[0]["apply"] is False


def test_cli_apply_requires_explicit_flag(monkeypatch, tmp_path, capsys):
    database = tmp_path / "atomic.db"
    database.touch()
    store = _Store()
    monkeypatch.setattr(
        cli, "SQLiteAtomicMultiAngleBatchStore", lambda _path: store
    )
    assert cli.main(["--sqlite", str(database), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "apply"
    assert store.calls[0]["apply"] is True


def test_cli_never_uses_aws_without_explicit_confirmation(capsys):
    assert cli.main(["--dynamodb-table", "production", "--region", "us-east-1"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert "--allow-aws" in payload["error"]


def test_cli_backend_failure_is_structured_and_nonzero(
    monkeypatch, tmp_path, capsys
):
    database = tmp_path / "atomic.db"
    database.touch()
    monkeypatch.setattr(
        cli,
        "SQLiteAtomicMultiAngleBatchStore",
        lambda _path: _Store(error=RuntimeError("authority unavailable")),
    )
    assert cli.main(["--sqlite", str(database)]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "status": "error",
        "error_type": "RuntimeError",
        "error": "authority unavailable",
    }


def test_run_constructs_aws_client_only_after_all_explicit_inputs():
    calls = []
    store = _Store()

    class _Boto:
        @staticmethod
        def client(service, *, region_name):
            calls.append((service, region_name))
            return object()

    args = Namespace(
        sqlite=None, dynamodb_table="atomic-prod", region="us-east-1",
        allow_aws=True, stale_seconds=600, apply=False,
    )
    result = cli.run(
        args, boto3_module=_Boto,
        dynamodb_store_factory=lambda **_kwargs: store,
    )
    assert calls == [("dynamodb", "us-east-1")]
    assert result["mode"] == "dry-run"
