# AIMS Supplier And Source Cards

| Field | Value |
| --- | --- |
| Document ID | AIMS-SUP-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | Supplier owner pending / CISO, CPO, Compliance Counsel approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual and on supplier/source change / set on approval |
| Classification | internal draft; no secrets or proprietary provider terms |
| Change summary / supersedes | expands issue #1243 supplier/source evidence cards / v0.1 draft |
| Repository path | `docs/aims/07-suppliers/supplier-and-source-cards.md` |

Supplier cards record the evidence TrustForge needs before external data, model, or infrastructure dependencies can be treated as controlled. They are not vendor certifications and do not copy external license text.

## Source And Supplier Register

| Card ID | Supplier/source kind | Use in TrustForge | Owner | Status | Required evidence | Current evidence URI | Gap / blocker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS-SUP-DATA-001 | Crypto market data APIs and CSV snapshots | price, OHLCV, market metrics, source-kind diversity | Product owner | partial implementation | source name, freshness, retention, exclusion reason, source-kind distribution | issue #1340; runtime snapshot examples in issue body | rich-source preservation test and report distribution evidence pending |
| AIMS-SUP-NEWS-001 | News or narrative sources | low-confidence corroborating or contrarian claims | Product owner | planned | source kind, confidence boundary, excluded-claim rationale, copyright-safe snippets | issue #1340 | approved low-confidence handling and representative claim sampling pending |
| AIMS-SUP-MODEL-001 | AWS Bedrock / LLM provider | synthesis and analysis narrative | Engineering owner | partial implementation | cost gate, prompt authority boundary, no secrets/URLs/trust-score authority delegated to model | issue #1342; issue #1406 | cost/security reviewer evidence needed for dual-child runs |
| AIMS-SUP-INFRA-001 | GitHub and repository workflow | issue, PR, review, test and evidence traceability | AIMS manager | aligned draft | branch, commit, PR, reviewer request, diff/test evidence | repository metadata and PR workflow | reviewer assignment can be blocked by self-review rules |
| AIMS-SUP-LEGAL-001 | Licensed standards and legal advice | EN 18286/EU AI Act clause-level review | Compliance Counsel | blocked | lawful licensed standard text, official bibliographic status, counsel disposition | issue #1264 | licensed authoritative text and counsel approval unavailable |

## Broken-Link And Evidence Checks

| Check | Rule | Status |
| --- | --- | --- |
| Repository path links | Every cited local path must exist at review time. | planned; reviewer runs `test -e` or equivalent |
| Issue/PR links | External issue/PR URIs must point to the TrustForge repository and remain readable by reviewers. | planned |
| Evidence URI sufficiency | A source card cannot rely on an issue title alone when runtime behavior is claimed; replayable artifact or test evidence is required. | planned |
| Public claim safety | Supplier cards may say `aligned draft`, `gap assessment`, or `preparation`; they must not state supplier certification, legal compliance, or CE readiness without explicit approval. | aligned draft |

## Assessment Disposition

TrustForge's current disposition for supplier/source controls is `aligned draft` for repository workflow controls, `partial implementation` for runtime evidence and model-provider controls, and `delegated gap assessment` for licensed legal-text controls. No row is marked certified or compliant.
