# AIMS Document And Communication Control

| Field | Value |
| --- | --- |
| Document ID | AIMS-SUPPORT-DOC-COMM-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | AIMS manager pending / CEO and Compliance Counsel approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual and on material AIMS change / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1242 communication matrix and document lifecycle controls / v0.1 draft |
| Repository path | `docs/aims/05-support/document-and-communication-control.md` |

This draft controls AIMS communications and documents. It prohibits unsupported public conformity claims until the relevant standard text, role classification, review findings, and authorized approvals exist.

## Communication Matrix

| Event | Audience | Owner | Approver | Channel | Deadline | Evidence URI | Required wording boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS policy or scope change | AIMS roles and affected internal owners | AIMS manager | CEO | repository PR and internal announcement | before effective date | PR URL plus approval record | state whether the change is draft, approved, or obsolete |
| Material AI system behavior or lifecycle-control change | Product, engineering, security, compliance | Engineering owner | Product owner and CISO when security-impacting | PR, release note, management-review pack | before release gate | linked issue/PR, test evidence, lifecycle row | do not claim operational effectiveness without test/replay evidence |
| Material AI incident or evidence-integrity issue | Executive, security, product, legal; external parties only when legally required | Security owner | CISO and Compliance Counsel | incident channel, legal-approved external notice if required | initial internal notice within 1 business day after confirmation | incident record, containment record, CAPA link | external notice must be legally approved and fact-bound |
| Risk acceptance or residual-risk change | Accountable executive and affected control owners | Risk owner | CEO plus CISO/CPO by risk type | risk register and management-review pack | before risk is treated as accepted | risk row, acceptance approval, review date | accepted risk must include revocation condition |
| External ISO/IEC 42001, EN 18286, EU AI Act, certification, compliance, or CE-related statement | Intended external audience | Compliance reviewer | Compliance Counsel and CEO | approved publication channel only | before publication | legal source review, approved text, approval timestamp | before certification, use only limited phrases such as "readiness draft", "gap assessment", or "certification preparation"; never unqualified `certified`, `compliant`, `conforms`, or `CE-ready` |

## Document Control Rules

| Control area | Rule | Evidence URI |
| --- | --- | --- |
| Naming | AIMS documents use stable paths under `docs/aims/` and a `Document ID` that does not change between versions. | repository path and PR URL |
| Version | Drafts use `0.x-draft`; approved documents use an approved version and effective date; obsolete documents retain superseded version metadata. | document header and approval record |
| Approval | No document is effective until approver, approval date, effective date, and review cadence are filled. | signed approval or PR review URI |
| Change control | Material changes require rationale, affected documents, reviewer list, and disposition of findings. | PR body or change record |
| Retention | Approved and obsolete records must keep exact content, approval evidence, superseding link, and access decision. | repository history or controlled archive URI |
| Access | Public repository documents must not contain secrets, personal training records, proprietary standard text, or confidential legal advice. | reviewer checklist |
| Obsolete handling | Obsolete documents must be marked `obsolete`, cite the replacement, and remain discoverable for audit. | obsolete marker and superseding URI |

## External Claims Guardrail

Until an authorized certification body, legal counsel, and accountable executive have approved a precise claim, TrustForge may say only that an artifact is a `draft`, `gap assessment`, `readiness artifact`, or `certification preparation` record. This applies to web copy, reports, sales material, README text, and customer-facing status messages.
