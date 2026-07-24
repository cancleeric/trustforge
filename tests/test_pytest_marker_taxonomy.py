"""Pytest marker taxonomy regressions for issue #480."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_perf_marker_taxonomy_is_registered():
    """slow/network/serial/subprocess markers should be explicit and documented."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    marker_by_name = {marker.split(":", 1)[0]: marker for marker in markers}

    assert marker_by_name["slow"].startswith("slow: tests that are expected")
    assert marker_by_name["network"].startswith("network: tests that perform")
    assert marker_by_name["serial"].startswith("serial: tests that must not run")
    assert marker_by_name["subprocess"].startswith("subprocess: tests that spawn")
