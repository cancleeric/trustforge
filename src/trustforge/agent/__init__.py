"""Agent 編排層：信任 brief → 結構化 Report + Evidence List + Execution Log。"""
from .orchestrator import build_report
from .shadow import (
    ShadowAccumulator,
    ShadowParityResult,
    record_shadow_run,
    reset_shadow_accumulator,
    shadow_diagnostics,
)

__all__ = [
    "build_report",
    "ShadowAccumulator",
    "ShadowParityResult",
    "record_shadow_run",
    "reset_shadow_accumulator",
    "shadow_diagnostics",
]
