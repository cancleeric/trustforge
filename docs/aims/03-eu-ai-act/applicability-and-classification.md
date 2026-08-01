# EU AI Act Applicability and Classification Record

> Document ID: AIMS-EU-APP-001
>
> Version: 0.1-draft
>
> Status: Draft — classification pending legal approval — non-effective
>
> Owner: CPO (proposed)
>
> Approver: Compliance Counsel (required; not yet approved)
>
> Parent tracking issue: #1264
>
> This document slice: #1265 / PR-A

## Proposed intended purpose

Proposed statement: TrustForge would be positioned as an evidence-linked cryptocurrency market-analysis AI agent that supports informed analysis and would not make or substantially determine decisions in the listed natural-person domains.

This is a proposal, not a verified description of the product. Product behavior, marketing claims, contracts, instructions for use, deployment facts and actual customer use are `unknown / evidence pending`; repository README text cannot prove intended purpose. The statement cannot be used as an approved instruction for use until those facts are reconciled and CPO plus Compliance Counsel approval is recorded against an exact version.

## Prohibited and out-of-scope uses

- Determining or materially influencing eligibility, ranking or treatment of a natural person in an EU AI Act Annex III domain.
- Operating as a safety component of a regulated product without a separate conformity assessment.
- Presenting analysis as guaranteed financial performance, regulated personalised advice or an autonomous execution mandate.
- Removing human responsibility for decisions made using a TrustForge output.
- Sending customer PII outside production, including to development, test, review prompts, screenshots, logs or external AI services.
- White-label, resale or substantial modification that changes the intended purpose without reassessment.

## Economic operator assessment

| Question | Preliminary state | Evidence or decision needed |
|---|---|---|
| Is HurricaneSoft the EU AI Act provider? | Pending | EU placing-on-market and service contract model |
| Is there an EU deployer? | Pending | Customer deployment and operational responsibility |
| Is an authorised representative required? | Pending | Provider establishment and Article 22 applicability |
| Are importer or distributor roles present? | Pending | EU supply-chain and white-label arrangements |
| Can a customer become a provider through substantial modification? | Possible | Contract controls and change-classification procedure |
| Which upstream providers are material? | Pending inventory | Model, data, AWS／Bedrock and other service contracts |

## Risk classification assessment

### Current preliminary position

The current market-analysis intended purpose does not, by itself, establish that TrustForge is a high-risk AI system. Final classification requires a documented assessment of Article 6, Annex I and Annex III against the approved intended purpose and actual EU use.

Status: `pending legal approval`. This is neither a high-risk nor a non-high-risk determination.

### Required checks

- Article 6(1) and Annex I regulated-product or safety-component conditions.
- Article 6(2) and Annex III use cases.
- Any applicable Article 6(3) exception analysis and required documentation.
- Prohibited-practice screening under Article 5.
- AI literacy obligations under Article 4.
- Transparency obligations under Article 50.
- General-purpose AI dependencies and downstream information requirements where applicable.
- Territorial scope, output use in the Union and economic operator roles.

## Article 50 feature, output and deployer assessment

Article 50 transparency is assessed independently from Article 6 risk classification. The following remain `not assessed`: whether users interact directly with an AI system; whether outputs are synthetic audio, image, video or text subject to marking or disclosure rules; whether an output is a deep fake or public-interest text; the provider's technical marking duties; and the deployer's disclosure duties. Product features, output modalities, audience, deployment context and actor allocation require exact-version evidence. An Article 50 result is not a limb of the high-risk classification decision.

The Article 50(2) transition must be checked separately against Regulation (EU) 2026/1744 and the current consolidated Article 113 before any deployment checkpoint is approved. This draft records no operative Article 50(2) date until Compliance Counsel verifies the exact amended text.

## Actor and misuse boundaries

- Article 25 provider transition is scoped to high-risk AI systems and requires a separate legal assessment of the statutory conditions, including placing a system on the market or putting it into service under one's own name or trademark, making a substantial modification, or changing the intended purpose of a system that was not classified as high-risk so that it becomes high-risk under Article 25(1)(c). It is not a generic transition rule for every AI system and is not the same as customer misuse.
- Off-label actual use and reasonably foreseeable misuse are separate records. Neither automatically proves an Article 25 provider transition.
- Articles 17 and 72 are assessed conditionally for a high-risk-system provider.
- Article 26 deployer monitoring, use and escalation duties are assessed separately from provider controls.
- Article 73 reporting is assessed conditionally for the provider, with other actors' escalation interfaces recorded separately.
- Article 10 is conditional on high-risk applicability and must distinguish systems using techniques involving model training from systems not using such techniques. A production-only PII boundary, if verified, would not establish Article 10 compliance.

## Reclassification triggers

Reopen this assessment immediately when any of the following occurs. Block every affected use, release and contract at once; where already active, stop or isolate the affected path when safe and legally required. Escalate to Compliance Counsel, CISO, CPO and CEO. Unblocking requires recorded approval tied to the exact product, contract and release version:

- intended purpose, target market or marketing claim changes;
- a customer uses TrustForge for an Annex III or regulated-product decision;
- automated execution or natural-person profiling is introduced;
- a material model, dataset, supplier or decision threshold changes;
- the product is white-labelled, resold, substantially modified or placed on the EU market through a new channel;
- EU AI Act delegated acts, guidance, harmonised-standard citations or applicable case law changes;
- an incident, complaint, monitoring signal or authority request contradicts the existing classification assumptions.

## Approval record

| Decision | Approver | Date | Evidence | State |
|---|---|---|---|---|
| Intended purpose | — | — | — | Pending |
| Economic operator roles | — | — | — | Pending |
| Risk classification | — | — | — | Pending |
| Prohibited／out-of-scope uses | — | — | — | Pending |
