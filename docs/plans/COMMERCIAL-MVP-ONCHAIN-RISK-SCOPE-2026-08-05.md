# Commercial MVP On-chain Risk Trust Calibration Scope

> Date: 2026-08-05
> Issue: #1423
> Status: planning contract only; no production behavior, live provider, billing, or deployment authorization

## Purpose

This document defines the first commercial MVP boundary for TrustForge as an
on-chain risk intelligence trust-calibration product. The MVP is intentionally
narrow: it helps a buyer compare external crypto-risk evidence, understand
where sources agree or diverge, and export a traceable sample report.

The product promise is not "automated compliance" or "complete market truth".
The promise is a reproducible trust-calibration layer that shows what evidence
was considered, how confident the system is, and which assumptions still need
human or provider validation.

## MVP Inputs

The MVP accepts one scoped investigation target per run:

| Input kind | Accepted form | Boundary |
|---|---|---|
| Token | Symbol plus network when needed, for example `BTC`, `ETH`, or `USDT on Ethereum` | No guarantee that every chain or wrapped asset is supported |
| Wallet | Public wallet address plus chain/network | Public on-chain address only; no customer private keys or custodial secrets |
| Transaction | Public transaction hash plus chain/network | Used for evidence lookup and lineage, not legal attribution by itself |
| Entity | Named exchange, protocol, issuer, whale label, or sanctioned/flagged entity when a public source supplies the label | Entity labels must preserve source and timestamp; no unverified identity claim is promoted as fact |

Supported commercial questions are limited to risk calibration:

1. How trustworthy is the current risk picture for this target?
2. Which sources agree, disagree, or abstain?
3. What evidence supports the risk rationale?
4. What changed since the selected snapshot or report, when historical evidence is available?

Supported modes:

| Mode | Use |
|---|---|
| `single_target_risk` | One token, wallet, transaction, or entity risk calibration report |
| `source_divergence` | Compare source agreement and disagreement for the same target |
| `evidence_review` | Buyer-readable evidence lineage and limitations review |

Anything outside those modes is out of scope for the first MVP.

## MVP Outputs

Each MVP run must produce the following buyer-readable outputs:

| Output | Minimum content |
|---|---|
| Trust score | Numeric score, plain-language confidence label, scoring version, and timestamp |
| Source agreement and divergence | Which sources support, contradict, or cannot answer the question |
| Risk rationale | Short explanation of the strongest risk drivers and strongest limiting factors |
| Evidence lineage | Evidence IDs, provider/source names, source URL when available, `published_at`, `retrieved_at`, and source-state note |
| Exportable sample report | Markdown or PDF-ready report that can be shared in a POC packet without live secrets |

The output must clearly separate facts, inferences, and missing evidence. If
source data is stale, credential-gated, archive-required, or blocked, the report
must say so instead of filling the gap with current-state API results.

## Non-goals

The first MVP does not include:

1. A generic SaaS platform for all industries.
2. Full enterprise billing, subscription management, or usage metering.
3. Enterprise SSO or tenant administration beyond what existing TrustForge
   development infrastructure already supports.
4. Live expansion to unconstrained paid providers.
5. Legal, AML, sanctions, accounting, audit, or regulatory certification.
6. Claims that TrustForge independently proves a real-world identity or legal
   status for a wallet or entity.
7. Production deployment authorization.

Security, cost, licensing, and customer-data review are required before any live
paid provider, customer credential, or customer dataset is enabled.

## Under-three-minute Buyer Demo Path

The non-engineering demo path should fit in one guided screen recording or live
walkthrough:

1. Open the sample on-chain risk report for a preselected public target.
2. Read the headline trust score and confidence label.
3. Scan the source agreement/divergence section to see which sources align and
   where the report abstains.
4. Open one Evidence lineage row and verify the source name, timestamp, state,
   and content reference.
5. Export or download the sample report packet.

The demo must use non-secret fixtures or public sample data. It must not require
live Arkham, Whale Alert, exchange, customer, AWS, or paid-provider credentials.

## Acceptance Gate

Before this scope is treated as a commercial MVP contract, the following must be
true:

- Inputs, supported modes, outputs, and non-goals remain documented in this file.
- Follow-up connector and evidence-contract work references this scope instead
  of redefining the MVP.
- Sample reports state fixture/public-data limitations clearly.
- No document claims certification, production deployment, live-data access, or
  legal compliance unless backed by a separate approved evidence package.
