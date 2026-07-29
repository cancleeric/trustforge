# #994 Holder Entity-Resolution Vendor/Data Cost Feasibility

- Date: 2026-07-29
- Issues: [#994](https://github.com/cancleeric/trustforge/issues/994), remediation of
  [#872](https://github.com/cancleeric/trustforge/issues/872)
- Scope: research and procurement gate only
- Decision: **NO-GO for procurement and production integration**
- Authorization: no purchase, trial signup, credential creation, provider contact,
  data ingestion, or production wiring is authorized by this report

## 1. Executive decision

TrustForge cannot currently claim an entity-resolved holder-concentration value.
The technically honest state remains `unknown`, with no numeric substitute.

The public evidence supports only one transparent self-service candidate for a
bounded offline evaluation: Nansen. Its official documentation publishes a
USD 49/month annual-billing or USD 69/month monthly Pro subscription, plus
credit purchases; label calls cost 500 credits and USD 100 buys 100,000
credits. This means 200 label lookups consume approximately USD 100 of credits,
exclusive of the subscription. The free plan has 100 one-time credits and does
not provide premium labels, so it is not an entity-resolution evaluation.

Arkham, Chainalysis, TRM Labs and Elliptic expose relevant entity or
blockchain-intelligence capabilities but do not publish a complete,
decision-grade price, contractual freshness SLA, immutable label revision, or
redistribution grant in the public materials reviewed. Their cost is therefore
**quote required / unknown**, not an estimated number.

Even a paid trial does not make the dimension production-eligible. Before any
purchase, a provider must contractually satisfy:

1. reproducible point-in-time labels and balances;
2. stable revision or snapshot identifiers;
3. BTC, ETH and BNB Chain coverage at a documented capability level;
4. lawful storage of raw responses and publication of derived aggregates;
5. support for custodians, bridges, burns and locked supply;
6. explicit treatment of lost-key uncertainty;
7. an export and termination path;
8. bounded price, overage and SLA remedies.

No public candidate demonstrates all eight. The decision is therefore:

- **Now: NO-GO** for procurement or production.
- **Permitted next step only after separate CEO authorization:** a time-boxed,
  offline, non-production Nansen evaluation or competitive RFI.
- **Promotion:** remains blocked until two independent source families and the
  evidence-quality gates pass. A single vendor cannot promote the dimension.

## 2. Requirements and forbidden inference

### 2.1 Required semantic model

The measurement unit is beneficial holder, not address:

`address -> technical cluster -> controlling entity -> beneficial holder`

Each arrow needs evidence and uncertainty. A cluster is not automatically an
entity; an entity controlling an omnibus wallet is not automatically the
beneficial owner of its balance.

### 2.2 Required adjustments

| Class | Required treatment | Failure mode |
|---|---|---|
| Custodian/CEX | Separate operational controller from beneficial customers; do not assign an omnibus balance to the custodian | Makes exchanges appear to own customer assets |
| Bridge | Link escrowed canonical supply with issued wrapped representation; count economic units once | Double-counts canonical and wrapped supply |
| Burn | Exclude only provably unspendable supply under a versioned rule | Treats arbitrary inactive addresses as burned |
| Locked/staked | Retain beneficial ownership while separately marking liquidity/lock state | Mistakes lock contracts or validators for holders |
| Lost keys | Mark `unknown`; inactivity alone is not proof of loss | Converts a heuristic into a false supply adjustment |
| Cross-chain identity | Record chain-specific clusters and evidence-backed links; do not infer same-controller from matching address bytes alone | Incorrectly merges unrelated identities |

Top-address concentration is prohibited as a replacement. It conflates
custodial omnibus wallets, contracts, bridges and beneficial holders.

### 2.3 Minimum evidence contract

Every retained observation must include:

- `provider`, `product`, `contract_version`;
- chain, asset, block height and UTC `as_of`;
- response hash and immutable raw-response location;
- label/entity identifier and label revision;
- attribution method class and confidence;
- included addresses and chain-specific cluster identifiers;
- custodian, bridge, burn, lock and lost-key disposition;
- retrieval time, source freshness and license scope.

If a provider cannot supply an immutable revision, TrustForge may hash the raw
response for audit, but that does not prove the provider can reproduce the same
label later. The record must remain `non_reproducible`, and cannot be promoted.

## 3. Public-source comparison

The table distinguishes documented facts from procurement questions. Blank
public documentation is **unknown**, never inferred as absent or compliant.

| Candidate | Entity-resolution capability | Historical depth / PIT | Cross-chain coverage | Public license limits | Public SLA | Public cost | Disposition |
|---|---|---|---|---|---|---|---|
| Nansen API | Address common/premium labels, entity search, related wallets and entity/address balances are documented | Historical address balances are documented; Smart Money history has a rolling four-year window and daily EOD UTC snapshots. No public immutable label-revision contract found | Labels accept a documented chain list including BNB and ETH ecosystems; the reviewed list does not include BTC | Redistribution has separate official guidelines; contract must expressly allow stored evidence and derived public output | Daily Smart Money data is typically available by 07:00 UTC, but wording says timing may vary; not a contractual availability SLA | Pro USD 49/month annual or USD 69 monthly; 100k credits USD 100; common/premium label endpoint cost must be confirmed because official pages show inconsistent common-label credit values | **RFI candidate; offline trial only after authorization** |
| Arkham API | Official API page advertises address/entity intelligence, holders, labels, transaction logs and historical balances | Historical balances are within the service definition; no public immutable revision or historical label snapshot contract found | Public marketing presents multi-chain intelligence, but an exact BTC/ETH/BNB capability matrix was not found in reviewed primary sources | API agreement prohibits third-party disclosure, derivative works and publication/display of a compilation or directory without written consent | Not published | Subscription controls fees and usage; no public decision-grade price | **RFI candidate; no trial or integration** |
| Chainalysis KYT/Reactor | Official KYT docs define a cluster as addresses identified as controlled by one entity | Transaction timestamps and historical investigation are supported; no public immutable entity-label revision contract found | Exact holder-analysis capability by BTC/ETH/BNB requires written confirmation | Public MSA restricts bulk export, sharing, public disclosure, dataset combination and competitive benchmarking; intended use needs negotiated rights | Not published in reviewed materials | Quote required / unknown | **RFI candidate only; licensing risk high** |
| TRM BLOCKINT/Forensics | Official BLOCKINT page documents address-to-entity intelligence, balances, activity windows and transaction history | Defined-timeframe transaction history is documented; historical entity-label snapshots/revisions are not | Official page states 184+ chains and all native tokens on EVM chains; capability depth varies and must be confirmed for BTC/ETH/BNB holder use | Terms for retaining raw entity data and publishing derived concentration were not established from public product materials | Marketing reports average latency under 500 ms; this is not a contractual SLA | Quote required / unknown | **RFI candidate only** |
| Elliptic | Official developer material focuses on wallet/transaction screening and connected-entity risk analysis | No public holder-concentration PIT/revision contract found | Product coverage must be mapped specifically for BTC/ETH/BNB holder analysis | Storage, derivative-output and redistribution rights require contract review | Not published in reviewed materials | Quote required / unknown | **Not shortlist-ready without RFI answers** |

### 3.1 Reproducibility finding

None of the reviewed primary sources promises that a historical entity label can
be fetched later using an immutable label revision and return byte-equivalent
content. This is a blocking gap, not proof that the products lack internal
history. A vendor response and contract exhibit are required.

### 3.2 Historical-depth finding

Nansen is the only candidate in this review with a clearly documented public
window: its Smart Money historical holdings use a rolling four-year window.
That endpoint is not equivalent to a complete beneficial-owner map. The
historical depth of entity labels themselves remains unknown.

### 3.3 Methodology finding

Public product pages describe clusters, labels, related wallets or entity
intelligence, but do not disclose enough ground-truth methodology to calculate
false-merge and false-split rates for TrustForge's holder metric. A paid
evaluation must use a blinded labelled test set and report precision, recall,
coverage and disagreement by asset. Marketing counts are not acceptance
evidence.

## 4. Cost model

### 4.1 Known public cost

For Nansen only:

| Component | Official public value | Notes |
|---|---:|---|
| Pro subscription | USD 49/month annual billing or USD 69/month monthly | Starter 1,000 credits |
| Trial | 100 one-time credits | No top-ups and no premium labels |
| Credit pack | USD 100 / 100,000 credits | Credits expire after one year |
| Label calls | Official detailed pricing says 500 credits for all label endpoints | The endpoint overview separately shows common labels at 100 credits; obtain written clarification |
| Example: 200 label calls | About 100,000 credits / USD 100 | Excludes subscription, retries, balance calls and taxes |

This is an API-call illustration, not total-cost-of-ownership. It cannot price
the address universe needed for a full supply concentration measurement until
sampling design and pagination are known.

### 4.2 Quote-required total-cost template

Every RFI response must price:

- base subscription and minimum term;
- environment, seat and API-key fees;
- calls/credits, pagination and retry charging;
- historical archive or snapshot surcharge;
- BTC/ETH/BNB coverage surcharge;
- raw-response retention and derived-output rights;
- support, onboarding and security review;
- overage ceiling and alerts;
- tax and currency;
- termination export and post-termination retention.

Decision makers must compare 12-, 24- and 36-month:

`TCO = subscription + usage + archive + rights + onboarding + operations + exit`

Unknown entries invalidate the financial comparison. They are not zero.

### 4.3 Spend guardrails for a future evaluation

A separate authorization must define:

- fixed maximum total spend and expiration date;
- no auto-renewal and no automatic top-ups;
- one isolated non-production credential;
- rate and daily cost limits;
- named cost owner and weekly receipt;
- immediate stop on license ambiguity, data-quality failure or budget breach.

## 5. Freshness, SLA and quality acceptance

Marketing terms such as "real-time" or an average response latency are not an
availability, correction or label-freshness SLA. The RFI must obtain:

| Requirement | Minimum contractual answer |
|---|---|
| API availability | monthly uptime target, measurement and service credit |
| Chain ingestion | maximum lag by BTC/ETH/BNB |
| Entity-label freshness | maximum publication lag and correction policy |
| Historical correction | immutable revision, changelog and retrieval path |
| Incident notice | notification deadline and status channel |
| Support | severity definitions and response/resolution targets |
| Deprecation | minimum notice and migration overlap |

Technical acceptance requires 30 consecutive days of measurements, not a sales
claim. Every daily sample records ingest lag, API error/latency, revision drift,
coverage, cross-provider disagreement, false merge/split and cost.

## 6. Evaluation design after authorization

### Phase 0: RFI and legal screen

No credentials or purchase. Obtain written answers for capability, revision,
SLA, security, data rights, total cost and exit. Reject candidates that prohibit
retaining evidence or publishing the derived holder metric.

### Phase 1: Offline blinded proof

Use an allowlisted, non-production environment and a fixed BTC/ETH/BNB corpus.
The corpus must include:

- known custodian omnibus and deposit addresses;
- canonical bridge escrow and wrapped representations;
- provable burn endpoints;
- staking, vesting and timelock contracts;
- long-inactive addresses deliberately labelled as unknown, not lost;
- negative controls that must not merge.

No provider result enters an LLM prompt, user report or production database.

### Phase 2: Dual-provider comparison

Do not calculate a production concentration value until two independent source
families cover the same point-in-time sample. Compare cluster/entity overlap,
balance reconciliation, exclusions and disagreements. Vendor labels do not
resolve beneficial ownership inside custodial omnibus balances; unresolved
custodial supply remains unknown.

### Phase 3: Promotion decision

Promotion requires all contractual, quality, cost and reproducibility gates.
Failure leaves the dimension `unknown` with zero contribution. No fallback
address concentration or imputed numeric value is allowed.

## 7. Provider exit and replacement strategy

The architecture must treat the provider as replaceable evidence, not the
canonical truth.

1. Store provider-neutral observations and adapter version.
2. Retain contract-permitted raw responses, hashes, retrieval metadata and
   normalized outputs.
3. Keep vendor entity IDs in a namespaced mapping; never make them TrustForge
   primary keys.
4. Rebuild the same PIT corpus through a replacement adapter before cutover.
5. Run at least 30 days dual-read and quantify drift.
6. Fail closed to `unknown` if the old provider expires before replacement
   passes.
7. Revoke credentials, stop jobs, export permitted evidence and obtain deletion
   confirmation.
8. Preserve methodology receipts without retaining data beyond licensed terms.

Exit triggers include price increase, unbounded overage, SLA breach, material
coverage regression, unexplained label revision, adverse license change,
security incident or inability to export audit evidence.

## 8. Go/no-go matrix

| Gate | Current state | Required to become GO |
|---|---|---|
| Beneficial-holder semantics | BLOCK | Demonstrate custodian look-through or explicitly quantify unknown custodial supply |
| BTC/ETH/BNB capability | BLOCK | Written per-chain method and historical-depth matrix |
| PIT reproducibility | BLOCK | Immutable revision/snapshot and replay evidence |
| Rights | BLOCK | Store raw evidence and publish derived metrics contractually allowed |
| SLA/freshness | BLOCK | Contractual lag, uptime, correction and deprecation terms |
| Independent source families | BLOCK | Two qualified providers or one provider plus a genuinely independent reproducible source |
| Quality | BLOCK | Blinded precision/recall/coverage and disagreement thresholds pass |
| Cost | BLOCK | Complete capped 12/24/36-month TCO |
| Exit | BLOCK | Export, dual-run and deletion rights accepted |

**Final recommendation: NO-GO.** Keep `holder_concentration=unknown`. Do not buy,
sign up, contact a vendor, ingest data, wire production, or publish a numeric
holder-concentration score under #872/#994. A future RFI or offline trial needs
new explicit CEO authorization and Harper cost/security review.

## 9. Primary sources reviewed

Accessed 2026-07-29:

1. [Nansen API credits and pricing](https://docs.nansen.ai/about/credits-and-pricing-guide)
2. [Nansen address labels API](https://docs.nansen.ai/api/profiler/address-labels)
3. [Nansen endpoint overview](https://docs.nansen.ai/about/endpoints-overview)
4. [Nansen data methodology and historical depth](https://docs.nansen.ai/guides/data-methodology-and-technical-reference)
5. [Arkham blockchain data API](https://arkm.com/api/)
6. [Arkham API terms](https://arkm.com/api-terms-of-service)
7. [Chainalysis KYT API reference](https://kytdoc.kyt-dev.e.chainalysis.com/)
8. [Chainalysis Master Subscription Agreement](https://www.chainalysis.com/msa0522/)
9. [TRM BLOCKINT API](https://www.trmlabs.com/blockchain-intelligence-platform/blockint-api)
10. [Elliptic developer API introduction](https://developers.elliptic.co/docs/ai-api-introduction)

## 10. Acceptance checklist

- [x] Reproducibility compared.
- [x] Historical depth compared without inventing unavailable values.
- [x] Cross-chain/entity-resolution method compared.
- [x] Licensing and derivative-output risk compared.
- [x] Freshness and SLA separated from marketing claims.
- [x] Public cost reported; quote-required cost remains unknown.
- [x] Custodians, bridges, burns, locked supply and lost-key uncertainty covered.
- [x] Provider exit and replacement strategy defined.
- [x] Explicit no-go issued.
- [x] No purchase, provider contact, credential or production integration made.
