"""Agent 編排層：信任 brief → 結構化 Report + Evidence List + Execution Log。"""
from .kernel_canary import (
    CanaryState,
    canary_active,
    full_kernel_active,
    get_canary_state,
    should_use_kernel,
)
from .orchestrator import build_report

__all__ = [
    "build_report",
    "CanaryState",
    "canary_active",
    "full_kernel_active",
    "get_canary_state",
    "should_use_kernel",
]
