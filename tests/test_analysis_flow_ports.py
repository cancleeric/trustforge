from __future__ import annotations

from pathlib import Path

import trustforge.analysis_flow_ports as ports
import trustforge.analysis_presentation as presentation
import trustforge.web as web


def test_bedrock_port_fails_closed_when_provider_raises():
    original = ports._bedrock_allowed_provider
    try:
        ports.register_bedrock_allowed(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ports.bedrock_allowed() is False
    finally:
        ports.register_bedrock_allowed(original)


def test_daemon_composition_root_registers_live_policy():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_analysis_flow.py").read_text()
    assert "register_bedrock_allowed(lambda: web._bedrock_allowed())" in source


def test_web_compatibility_names_share_projection_implementation():
    assert web._aggregate_trust_components is presentation.aggregate_trust_components
    assert web._price_provenance_data is presentation.price_provenance_data
    assert web._public_evidence_dict is presentation.public_evidence_dict


def test_training_backend_legacy_import_surface_remains_available():
    from trustforge.training_backend import resolve_training_backend
    from trustforge.training_backend_resolver import resolve_training_backend as resolver

    assert resolve_training_backend is resolver
