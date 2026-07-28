from __future__ import annotations

import json
from pathlib import Path


def test_agentcore_config_points_to_real_trustforge_entrypoint():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "agentcore" / "agentcore.json").read_text())
    runtime = next(item for item in config["runtimes"] if item["name"] == "TrustForge")

    assert runtime["codeLocation"] == "."
    assert runtime["entrypoint"] == "app/TrustForge/main.py"
    assert (root / runtime["entrypoint"]).is_file()


def test_agentcore_entrypoint_calls_ported_runtime():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "TrustForge" / "main.py").read_text()
    assert "BedrockAgentCoreApp" in source
    assert "invoke_payload(payload)" in source
