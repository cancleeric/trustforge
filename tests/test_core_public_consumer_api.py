"""Public core API consumer regressions for issue #453."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_external_consumer_can_run_kernel_without_app_imports():
    """A non-TrustForge consumer should use trustforge_core without app imports."""
    repo_root = Path(__file__).resolve().parents[1]
    code = """
import json
import sys

from trustforge_core import KERNEL_CONTRACT_VERSION, KernelClaim, KernelDocument, KernelInput, run_kernel

doc = KernelDocument(
    id="p0",
    kind="price",
    source="fixture-price",
    text="BTC close rose 3%",
    timestamp=1000.0,
)
claim = KernelClaim(
    id="c0",
    text="BTC close rose 3%",
    document=doc,
    claim_type="fact",
    direction="bullish",
)
inp = KernelInput(
    claims=(claim,),
    pit_epoch=1000.0,
    coin="BTC",
    query="BTC outlook",
    contract_version=KERNEL_CONTRACT_VERSION,
)
first = run_kernel(inp)
second = run_kernel(inp)
print(json.dumps({
    "direction": first.direction,
    "confidence": first.confidence,
    "deterministic": first == second,
    "app_imported": "trustforge.pipeline" in sys.modules or "trustforge.agent.orchestrator" in sys.modules,
}, sort_keys=True))
"""
    env = {
        **os.environ,
        "PYTHONPATH": f"{repo_root / 'src'}{os.pathsep}{repo_root}",
    }

    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["deterministic"] is True
    assert payload["app_imported"] is False
    assert payload["direction"] in {"bullish", "bearish", "neutral", "偏多", "偏空", "中性"}
    assert 0.0 <= payload["confidence"] <= 1.0
