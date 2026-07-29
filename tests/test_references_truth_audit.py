from __future__ import annotations

import pytest

from scripts.check_references_truth_audit import verify_audit, verify_references_export


def test_references_truth_audit_is_conservative_and_reproducible():
    checks = verify_audit()

    assert "Taiwan regulatory sources are not marked verified" in checks
    assert "production deploy workflow is documented as disabled" in checks
    assert "only the approved hourly release train and optional CI workflows are active" in checks


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


def test_references_export_missing_is_optional_by_default(tmp_path):
    missing = tmp_path / "references.html"

    checks = verify_references_export(missing)

    assert checks == [f"references export not present; skipped {missing}"]


def test_references_export_can_be_required_for_public_sync(tmp_path):
    missing = tmp_path / "references.html"

    with pytest.raises(AssertionError, match="references export is required but missing"):
        verify_references_export(missing, require_present=True)


def test_references_export_rejects_stale_verified_public_statuses(tmp_path):
    references = tmp_path / "references.html"
    references.write_text(
        "\n".join(
            [
                "<div>HOYA BIT OHLCV ✅ verified</div>",
                "<div>HOYA BIT live ticker ✅ verified</div>",
                "<div>GitHub Actions CI ✅ verified</div>",
                "<div>Production Deploy deploy-production.yml.disabled</div>",
                "<div>AgentCore runtime routing 🟡 implemented-not-verified</div>",
                "<div>manipulation detection informational-only 不扣分</div>",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="HOYA BIT live ticker"):
        verify_references_export(references)


def test_references_export_accepts_conservative_public_statuses(tmp_path):
    references = tmp_path / "references.html"
    references.write_text(
        "\n".join(
            [
                "<div>HOYA BIT OHLCV ✅ verified</div>",
                "<div>HOYA BIT live ticker ⚠ blocked</div>",
                "<div>GitHub Actions CI .disabled 停用</div>",
                "<div>Production Deploy deploy-production.yml.disabled 停用</div>",
                "<div>AgentCore runtime routing 🟡 implemented-not-verified 未驗證</div>",
                "<div>manipulation detection informational-only 不扣分</div>",
            ]
        ),
        encoding="utf-8",
    )

    checks = verify_references_export(references)

    assert "public references export rejects stale verified statuses" in checks
