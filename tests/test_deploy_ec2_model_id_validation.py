"""Regression tests for BEDROCK_MODEL_ID validation in deploy_ec2.sh.

Issue #776: versioned Bedrock model IDs (containing colons, e.g.
`anthropic.claude-3-5-sonnet-20241022-v2:0`) were incorrectly rejected by
the character allowlist. The fix adds `:` to the regex.

This test extracts the MODEL validation regex from deploy_ec2.sh and exercises
it directly in Python to ensure colon-bearing IDs pass and truly invalid
characters remain blocked.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DEPLOY_EC2 = _REPO / "deploy" / "deploy_ec2.sh"


def _extract_model_regex() -> str:
    """Extract the MODEL validation regex from deploy_ec2.sh."""
    script = _DEPLOY_EC2.read_text(encoding="utf-8")
    # Line looks like: [[ "$MODEL" =~ ^[A-Za-z0-9._:-]+$ ]]
    m = re.search(r'"\$MODEL"\s*=~\s*(\^[^\s;]+)', script)
    if not m:
        pytest.skip("Cannot locate MODEL regex in deploy_ec2.sh")
    return m.group(1)


@pytest.fixture(scope="module")
def model_regex() -> re.Pattern[str]:
    """Compile the MODEL allowlist regex from deploy_ec2.sh."""
    raw = _extract_model_regex()
    return re.compile(raw)


# --- Valid model IDs (must be accepted) ---

_VALID_MODEL_IDS = [
    # Plain model ID
    "anthropic.claude-3-5-sonnet-20241022-v2",
    # Versioned with colon (#776 regression)
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "amazon.titan-text-express-v1:0",
    "meta.llama3-70b-instruct-v1:0",
    # Multiple colons (cross-region inference profile style)
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    # Only dots and hyphens
    "my-model.v2",
    # Underscore
    "my_model_v1",
]

# --- Invalid model IDs (must be rejected) ---

_INVALID_MODEL_IDS = [
    # Spaces
    "anthropic claude",
    # Shell metacharacters
    "model;rm -rf /",
    "model$(whoami)",
    "model`id`",
    # Slashes (path traversal)
    "../etc/passwd",
    "model/subpath",
    # Other dangerous chars
    "model&bg",
    "model|pipe",
    "model>redirect",
    "model<input",
    # Quotes
    'model"quoted',
    "model'quoted",
]


@pytest.mark.skipif(not _DEPLOY_EC2.exists(), reason="deploy_ec2.sh not found")
class TestModelIdValidation:
    """Ensure deploy_ec2.sh MODEL regex accepts valid IDs and blocks invalid ones."""

    @pytest.mark.parametrize("model_id", _VALID_MODEL_IDS)
    def test_valid_model_ids_accepted(
        self, model_regex: re.Pattern[str], model_id: str
    ) -> None:
        assert model_regex.match(model_id), (
            f"Valid model ID rejected: {model_id!r}"
        )

    @pytest.mark.parametrize("model_id", _INVALID_MODEL_IDS)
    def test_invalid_model_ids_rejected(
        self, model_regex: re.Pattern[str], model_id: str
    ) -> None:
        assert not model_regex.match(model_id), (
            f"Invalid model ID accepted: {model_id!r}"
        )

    def test_colon_in_allowlist_regression_776(
        self, model_regex: re.Pattern[str]
    ) -> None:
        """Explicit regression: colon must be permitted (issue #776)."""
        versioned = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert model_regex.match(versioned), (
            "Regression #776: versioned Bedrock model ID with colon was rejected"
        )
