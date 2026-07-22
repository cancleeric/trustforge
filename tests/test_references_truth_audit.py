from __future__ import annotations

import pytest

from scripts.check_references_truth_audit import verify_audit


def test_references_truth_audit_is_conservative_and_reproducible():
    checks = verify_audit()

    assert "Taiwan regulatory sources are not marked verified" in checks
    assert "production deploy workflow is documented as disabled" in checks


def test_references_truth_audit_rejects_taiwan_sources_marked_verified(tmp_path):
    audit = tmp_path / "REFERENCES-TRUTH-AUDIT.md"
    audit.write_text(
        "\n".join(
            [
                "✅ verified",
                "🟡 implemented-not-verified",
                "🔬 research/experimental",
                "📚 reference/planned",
                "⛔ excluded",
                "⚠ blocked-external",
                "HOYA BIT live ticker ⚠ blocked-external",
                "AgentCore runtime routing 🟡 implemented-not-verified",
                "Guo et al. Calibration ✅ verified",
                "Self-RAG 📚 reference/planned",
                "manipulation detection 🟡 implemented-not-verified",
                "MOPS / FSC / TWSE / TPEx ✅ verified",
                "Production Deploy deploy-production.yml.disabled",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="Taiwan regulatory sources"):
        verify_audit(audit)
