from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
AIMS_ROOT = ROOT / "docs" / "aims"


READINESS_DOCS = [
    AIMS_ROOT / "05-support" / "competency-and-training-register.md",
    AIMS_ROOT / "05-support" / "document-and-communication-control.md",
    AIMS_ROOT / "05-support" / "document-lifecycle-trace.md",
    AIMS_ROOT / "06-lifecycle" / "lifecycle-control-matrix.md",
    AIMS_ROOT / "07-suppliers" / "supplier-and-source-cards.md",
    AIMS_ROOT / "03-risk" / "risk-methodology-and-register.md",
    AIMS_ROOT / "08-measurement" / "kpi-and-monitoring-register.md",
    AIMS_ROOT / "09-audit" / "audit-programme-and-report.md",
    AIMS_ROOT / "10-capa" / "capa-and-management-review.md",
    AIMS_ROOT / "soa" / "statement-of-applicability.md",
]


FORBIDDEN_UNQUALIFIED_CLAIMS = re.compile(
    r"\b(?:certified|compliant|conformant|CE-ready)\b", re.IGNORECASE
)


def test_aims_readiness_docs_exist_and_stay_draft_bound():
    for doc in READINESS_DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "draft" in text.lower(), doc
        assert "unapproved" in text.lower() or "未核准" in text, doc


def test_aims_docs_do_not_make_unqualified_external_claims():
    for doc in READINESS_DOCS:
        text = doc.read_text(encoding="utf-8")
        for match in FORBIDDEN_UNQUALIFIED_CLAIMS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            excerpt = text[max(0, match.start() - 160) : match.end() + 120].lower()
            assert (
                "prohibit" in excerpt
                or "forbidden" in excerpt
                or "never" in excerpt
                or "no row marked" in excerpt
                or "no row is marked" in excerpt
                or "must not" in excerpt
            ), (
                doc,
                line,
                match.group(0),
            )


def test_support_package_covers_issue_1242_required_fields():
    competency = (AIMS_ROOT / "05-support" / "competency-and-training-register.md").read_text(
        encoding="utf-8"
    )
    communication = (AIMS_ROOT / "05-support" / "document-and-communication-control.md").read_text(
        encoding="utf-8"
    )
    lifecycle = (AIMS_ROOT / "05-support" / "document-lifecycle-trace.md").read_text(
        encoding="utf-8"
    )

    for required in [
        "Necessary competence",
        "Current evidence",
        "Gap",
        "Reinforcement plan",
        "Owner",
        "Review date",
        "Planned",
        "Completed",
        "Verified",
    ]:
        assert required in competency

    for required in ["Audience", "Owner", "Approver", "Channel", "Deadline", "Evidence URI"]:
        assert required in communication

    for required in ["draft -> in review", "in review -> approved", "approved -> obsolete"]:
        assert required in lifecycle


def test_soa_references_existing_local_evidence_paths():
    soa = (AIMS_ROOT / "soa" / "statement-of-applicability.md").read_text(encoding="utf-8")
    for path in re.findall(r"`(docs/aims/[^`]+)`", soa):
        assert (ROOT / path).exists(), path
