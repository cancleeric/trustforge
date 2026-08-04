# On-Chain Risk Trust Calibration MVP Scope

## Purpose

This MVP keeps TrustForge focused on one buyer-readable use case: calibrating whether on-chain risk evidence is reliable enough to support a commercial POC decision. It is a planning and contract boundary only; it does not enable live paid providers or change production behavior.

## Inputs

Supported input types:

- `token`: a public asset symbol or contract address used to frame source collection.
- `wallet`: a public wallet or exchange-labelled address used for exposure and flow review.
- `transaction`: a public transaction hash used for event-level risk explanation.
- `entity`: a public organization, exchange, protocol, or labelled cluster name.

Supported question boundaries:

- Source agreement: do independent sources describe the same event or entity risk?
- Divergence: where do provider narratives, timestamps, or labels disagree?
- Rationale: what evidence explains a higher or lower trust score?
- Lineage: which source payload, hash, retrieval time, and normalization version support the result?

Unsupported question boundaries:

- Legal, regulatory, accounting, tax, sanctions, or compliance certification.
- Investment advice or price prediction.
- Unlimited live-data expansion without provider license, credential, and cost review.

## Outputs

The MVP output package must contain:

- Trust score calibrated to the available evidence quality.
- Source agreement and divergence notes.
- Risk rationale written for a non-engineering buyer.
- Evidence lineage with provider, source URL, published time, retrieved time, content hash, raw payload reference, source state, and normalization version.
- Exportable sample report using non-secret fixture data.

## Non-Goals

- Generic SaaS workflow coverage.
- All industries and all external data markets.
- Billing, SSO, multi-tenant commercial provisioning, or live customer onboarding.
- Live Arkham, Whale Alert, or paid provider enablement.
- Current-state API data presented as historical archives.

## Three-Minute Demo Path

1. Open the on-chain risk sample report template and select the synthetic BTC transaction or wallet example.
2. Show the trust score, source agreement, and divergence panel first.
3. Expand Evidence lineage and point to provider, URL, published/retrieved time, hash, raw payload reference, and source state.
4. Close with the licensing/archive checklist so the buyer can see which items are ready, credential-gated, archive-required, or blocked.
