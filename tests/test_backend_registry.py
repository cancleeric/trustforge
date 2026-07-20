import pytest

from trustforge.backend_registry import (
    get_all_providers,
    get_provider,
    provider_snapshot,
    set_all_providers,
    set_provider,
)


def test_defaults_all_backend_providers_to_builtin(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json"))

    providers = get_all_providers()

    assert providers["memory"] == "builtin"
    assert providers["gateway"] == "builtin"
    assert all(value == "builtin" for value in providers.values())


def test_switches_one_provider_without_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json"))

    snapshot = set_provider("memory", "agentcore")

    assert get_provider("memory") == "agentcore"
    assert get_provider("policy") == "builtin"
    assert snapshot["restart_required"] is False
    assert snapshot["hot_config"] is True


def test_switches_all_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json"))

    set_all_providers("agentcore")

    assert all(value == "agentcore" for value in get_all_providers().values())


def test_rejects_unknown_keys_and_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json"))

    with pytest.raises(ValueError):
        set_provider("unknown", "builtin")
    with pytest.raises(ValueError):
        set_provider("memory", "unknown")


def test_snapshot_exposes_contract_metadata():
    snapshot = provider_snapshot({"memory": "builtin"})

    assert "memory" in snapshot["provider_keys"]
    assert snapshot["valid_providers"] == ["builtin", "agentcore"]
