from __future__ import annotations

import subprocess
import sys
import textwrap
import os
from pathlib import Path


def test_disabled_shadow_runtime_import_does_not_require_fcntl():
    script = textwrap.dedent(
        """
        import builtins
        import os

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ModuleNotFoundError("simulated non-POSIX platform")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        os.environ.pop("TRUSTFORGE_SHADOW_RUNTIME_ENABLED", None)
        os.environ.pop("KERNEL_SHADOW_OBSERVE", None)

        from trustforge.agent.shadow_runtime import observe_candidate

        result = observe_candidate(
            claims=(),
            scored=[],
            legacy_confidence=0.0,
            legacy_trust_raw=0.0,
            coin="BTC",
            question_type="analysis",
            query="test",
            request_id="import-test",
            pit_epoch=0.0,
            observed_epoch=0.0,
        )
        assert result.status == "not_observed"
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )

    assert completed.returncode == 0, completed.stderr
