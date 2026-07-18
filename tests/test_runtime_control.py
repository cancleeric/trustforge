from trustforge.runtime_control import runtime_control, set_runtime_enabled


def test_local_defaults_enabled_when_no_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.delenv("TRUSTFORGE_RUNTIME_SWITCH", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ENV", raising=False)
    monkeypatch.setenv("CACHE_BACKEND", "json")

    control = runtime_control()

    assert control.enabled is True
    assert control.source == "local_default"
    assert control.production is False


def test_cli_state_file_can_stop_and_start_local_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.delenv("TRUSTFORGE_RUNTIME_SWITCH", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ENV", raising=False)
    monkeypatch.setenv("CACHE_BACKEND", "json")

    stopped = set_runtime_enabled(False, reason="test stop")
    assert stopped.enabled is False
    assert stopped.source == "state_file"

    started = set_runtime_enabled(True, reason="test start")
    assert started.enabled is True
    assert started.source == "state_file"


def test_production_defaults_disabled_even_without_state(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.delenv("TRUSTFORGE_RUNTIME_SWITCH", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", raising=False)
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")

    control = runtime_control()

    assert control.enabled is False
    assert control.source == "production_default"
    assert control.production is True


def test_production_requires_two_explicit_env_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_SWITCH", "on")
    monkeypatch.delenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", raising=False)

    blocked = runtime_control()
    assert blocked.enabled is False
    assert blocked.source == "production_guard"

    monkeypatch.setenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", "1")
    allowed = runtime_control()
    assert allowed.enabled is True
    assert allowed.source == "env"
