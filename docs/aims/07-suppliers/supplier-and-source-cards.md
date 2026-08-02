| Field | Value |
| --- | --- |
| Document ID | AIMS-LIFE-SUP-001 |
| Version / status | 0.1-draft / draft, unapproved, non-effective |
| Owner / approver | CEO assignment pending / CEO approval pending |
| Approval record / effective date | pending / not-applicable (draft) |
| Review cadence / next review | set on approval / set on approval |
| Classification | internal-draft |
| Change summary / supersedes | establish supplier and source card draft / not-applicable (initial draft) |
| Repository path | `docs/aims/07-suppliers/supplier-and-source-cards.md` |

# Supplier And Source Cards

This draft records only verifiable repository knowledge and review placeholders. Unknowns remain `unknown` or `todo`; this document must not be used to imply vendor approval, service availability, or compliance certification.

## Source Cards

| Card ID | Source / supplier | Use in TrustForge | Credential boundary | Availability / degradation | Evidence URI | Status |
| --- | --- | --- | --- | --- | --- | --- |
| SUP-SRC-001 | HOYA BIT OHLCV | historical market data | unknown in this draft | history verified; live availability depends on runtime status | `README.md`; `docs/technical-docs/06-data-flow.md` | partial |
| SUP-SRC-002 | CoinGecko | market, sentiment, developer activity | public/keyless path documented; exact plan unknown | cache/degrade behavior requires runtime evidence | `docs/technical-docs/02-architecture.md`; `docs/technical-docs/06-data-flow.md` | partial |
| SUP-SRC-003 | CoinMarketCap | price cross-check | key-based; plaintext must not enter logs/responses | disabled or degraded when credential missing | `docs/technical-docs/00-evidence-map.md`; `docs/technical-docs/06-data-flow.md` | partial |
| SUP-SRC-004 | Etherscan | ETH whale transaction evidence | key-based query; sensitive fields sanitized | unavailable when credential/rate limit fails | `docs/technical-docs/06-data-flow.md` | partial |
| SUP-SRC-005 | Whale Alert / Arkham | large transfer and wallet-label evidence | key-based where applicable; no keys in URL/meta/log | stale-key and revocation behavior must be reviewed in linked security PRs | `docs/technical-docs/00-evidence-map.md`; GitHub PR review pending | partial |
| SUP-SRC-006 | DefiLlama | DeFi TVL and price context | public API assumptions require confirmation | may legitimately return no TVL for unsupported assets | `docs/technical-docs/06-data-flow.md`; `docs/technical-docs/08-trust-algorithm.md` | partial |
| SUP-SRC-007 | FSC / MOPS / TWSE / TPEx | Taiwan regulatory and market disclosure evidence | host allowlist and safe fetch boundary | fail closed or disclose unavailable source | `docs/technical-docs/00-evidence-map.md`; `docs/technical-docs/06-data-flow.md` | partial |
| SUP-SRC-008 | AWS Bedrock | semantic/narrative model calls | provider credentials outside repo; budget controls required | fail closed when budget, pricing, or ledger is unavailable | `docs/architecture/ARCHITECTURE-OVERVIEW.puml`; cost-control PR evidence pending | partial |

## Model And Tool Cards

| Card ID | Model / tool | Purpose | Known constraints | Required review before production reliance | Status |
| --- | --- | --- | --- | --- | --- |
| SUP-MDL-001 | Bedrock reviewer / narrative model | semantic review and narrative synthesis | cost and prompt-size limits must be enforced | cost/security review for unmetered autonomous calls | partial |
| SUP-MDL-002 | HolyShield / Aegis scanners | local active and static security scan evidence | scanner false positives and missing ML dependencies must be triaged | security owner review and repeat scan evidence | partial |
| SUP-MDL-003 | Calibration / trust scoring | source corroboration and score calibration | effectiveness evidence pending per release | independent validation and residual-risk review | planned |

## Supplier Change Rules

- New or materially changed source cards require owner, purpose, credential boundary, degradation path, and evidence URI before production reliance.
- Vendor marketing, compliance, or certification claims must be reviewed by Compliance Counsel before external use.
- Credentials, account identifiers, and private endpoint details must not be copied into supplier cards.
- If runtime evidence conflicts with this draft, runtime evidence wins and this document must be updated or marked stale.

## Open Reviews

| Review | Required reviewer | Status |
| --- | --- | --- |
| Product/source fitness | gray or equivalent CPO reviewer | pending |
| Security and credential boundary | harper or equivalent CISO reviewer | pending |
| Independent tabletop replay | independent reviewer | pending |
