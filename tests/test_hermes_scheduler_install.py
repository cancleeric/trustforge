"""Keep the scheduled Hermes runtime aligned with the packaged app layout."""
from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "install_hermes_scheduler.sh"
FETCH_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "install_fetch_scheduler.sh"
CANDIDATE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "deploy" / "prepare_backend_deploy_backup.sh"
)


def test_hermes_scheduler_sets_packaged_home_and_starts_timer():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "Environment=TRUSTFORGE_HOME=$APP_DIR" in script
    assert "Environment=TRUSTFORGE_HERMES_AUTONOMY_ENABLED=0" in script
    assert "OnUnitActiveSec=30min" in script
    assert "systemctl enable --now hermes-cycle.timer" in script
    assert "install_fetch_scheduler.sh" in script


def test_fetch_scheduler_probes_infrastructure_and_allows_upstream_degradation():
    script = FETCH_SCRIPT.read_text(encoding="utf-8")

    assert "ExecStartPre=/usr/bin/python3 scripts/fetch_scheduler.py --probe" in script
    assert "ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py --allow-partial" in script


def test_backend_candidate_is_healthy_before_primary_restart():
    installer = SCRIPT.read_text(encoding="utf-8")
    candidate = CANDIDATE_SCRIPT.read_text(encoding="utf-8")

    assert "prepare_backend_deploy_backup.sh" in installer
    assert "Environment=PORT=$CANDIDATE_PORT" in candidate
    assert "RuntimeMaxSec=5min" in candidate
    assert "Restart=no" in candidate
    assert 'curl -fsS "http://127.0.0.1:$CANDIDATE_PORT/api/health"' in candidate


def test_all_nginx_modes_have_candidate_backend_failover():
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy"
    for name in (
        "nginx.conf",
        "nginx-react-http.conf",
        "nginx-legacy.conf",
        "nginx-legacy-tls.conf",
    ):
        config = (deploy_dir / name).read_text(encoding="utf-8")
        assert "upstream trustforge_backend" in config, name
        assert "server 127.0.0.1:8080" in config, name
        assert "server 127.0.0.1:8081 backup;" in config, name
        assert "proxy_pass http://127.0.0.1:8080" not in config, name
