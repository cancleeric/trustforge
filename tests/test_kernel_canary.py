"""Canary health and deterministic bucketing tests (Issue #733)."""

import os
from unittest.mock import patch

from trustforge.agent.kernel_canary import (
    CanaryState,
    canary_active,
    full_kernel_active,
    should_use_kernel,
)


def test_canary_active_at_5_percent() -> None:
    with patch.dict(os.environ, {"KERNEL_CANARY_RATIO": "0.05"}):
        assert canary_active() is True
        assert full_kernel_active() is False


def test_canary_not_active_at_zero() -> None:
    with patch.dict(os.environ, {"KERNEL_CANARY_RATIO": "0"}):
        assert canary_active() is False
        assert full_kernel_active() is False


def test_full_kernel_at_one() -> None:
    with patch.dict(os.environ, {"KERNEL_CANARY_RATIO": "1.0"}):
        assert full_kernel_active() is True
        assert canary_active() is False


def test_should_use_kernel_deterministic() -> None:
    # Same inputs → same result
    with patch.dict(os.environ, {"KERNEL_CANARY_RATIO": "0.5"}):
        r1 = should_use_kernel(coin="BTC", query="q", ts="123")
        r2 = should_use_kernel(coin="BTC", query="q", ts="123")
        assert r1 is r2


def test_canary_state_records_errors() -> None:
    st = CanaryState()
    assert st.should_stop is False
    for _ in range(5):
        st.record_error()
    assert st.should_stop is True


def test_canary_state_successes_reset_streak() -> None:
    st = CanaryState()
    for _ in range(4):
        st.record_error()
    st.record_success()
    assert st.should_stop is False
    assert st.consecutive_errors == 0
