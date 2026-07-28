from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_kernel_canary_module_stays_removed():
    assert importlib.util.find_spec("trustforge.agent.kernel_canary") is None


def test_production_code_has_no_removed_kernel_canary_import():
    references = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        if "trustforge.agent.kernel_canary" in text:
            references.append(path)
    assert references == []
