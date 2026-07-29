# Issue #998 Phase 4 — external and elapsed-evidence reconciliation

- Audit commit inspected: `06a4c3d0840a6ea2739a47253fe8c4f3c7393a9e`
- Collected at: `2026-07-30T04:50:19+08:00`
- Scope: the 16 `EXTERNAL` criterion rows plus the explicitly required
  #870 control/governance source-family check
- Mutation boundary: read-only evidence inspection and documentation only;
  no feature code, canonical observation, receipt, disposition, or release
  artifact was changed

## Disposition

**REMAIN_SHADOW / RELEASE BLOCKED**

| Result | Count |
|---|---:|
| `PASS` | 1 |
| `REMAIN_SHADOW` | 4 |
| `BLOCKED_EXTERNAL` | 11 |
| Total external rows | 16 |

This is an availability snapshot, not a historical approval. The checked-in
promotion recommendation is still `block`, no authentic signed current
promotion disposition was available, and no real #877/#879 release-host
evidence bundle was found.

## Canonical source snapshot

| Source | SHA-256 / identity | Current observation |
|---|---|---|
| `data/intrinsic_promotion/receipt-current.json` | `70436e093f54ede40e278258b704469df1ddc4c802623e996c79f4a7421f8c21` | Commit-bound recommendation: 3 observations, 3 assets, 2 days, 1 eligible observation; decision `block`; labels not mature |
| `data/canary_control/canary_disposition.json` | `49990fe5935ce1fd228cc0e476a0e1fa6fd27ac665f20cb986225146c8288b9e` | Unsigned controller record: `remain_shadow`, G decision `block`, promotion path not exercised |
| `data/asset_intrinsic_records.json` | `89a99e94647d4b8bb11f7b54ae4da153ea44201605732008c162b7fb1f875a91` | BTC holder unknown; BNB all relevant holder/control/governance facts unknown; ETH holder/control/governance unknown |
| #994 authoritative feasibility report | `a986f4bd5eca03cf1f840c66a7310fc910cad90514f29acc9d3a499a2550fec1` | Procurement and production integration `NO-GO`; no entity-resolved dataset acquired |
| #872 licensing/freshness limits | `0c4309e170e5c54d664fabc717a793f7bc97bbd1022768685a7f251ae5780062` | Documents the required licensing, PIT, freshness and deduplication constraints |
| #875 GitHub issue snapshot | `0b1655377777209ee9c0ae477ac37cfe2b06dee6cd3f1ee77d2b1f13fe5bb4af` | Closed for the recommend-only engine; comment explicitly says the current receipt remains BLOCK and is not connected to automatic canonical production ingestion |
| #872 GitHub issue snapshot | `d6a0fa90743a0d1bd5f4a5179ab1f6a76437c7aa6e8b13ede82eca201b543dba` | Closed after #994; holder concentration remains unknown and no procurement is authorized |
| #877 GitHub issue snapshot | `7038ccf3af983875d5426c33804138b1c1f6093cca4b201b88bcc5be20125f45` | Closed historical implementation issue, but no real non-production release-host receipt is linked |
| #879 GitHub issue snapshot | `fc2d9cd575e252f808af028ee0d27919d617bb933895eeea59f794452990ee70` | Open; real authenticated topology and two-release evidence remain required |
| #994 GitHub issue snapshot | `c9edd897593612ce6cd9d4dbf9a744ebc370df552c4fc31475f4583781463e86` | Closed with `NO-GO`; no purchase, trial, credentials or production integration |
| #1035 GitHub issue snapshot | `6b12a8040067eb19503dab266660a4560f4832cd57ae65a61b525f4df35c6d82` | Open; typed independent control planes and canonical source-withdrawal replay remain absent |

GitHub issue snapshot digests are SHA-256 over the canonical sorted JSON
returned by `gh api repos/cancleeric/trustforge/issues/<number>` at collection
time. Repository file digests are over the exact checked-in bytes.

## #875 current gate and calibration evidence

The only checked-in current recommendation reports:

- eligible observations: **1** of 3;
- distinct assets: **3**;
- elapsed span: **2 days**;
- minimum required: 200 observations, 5 assets, 30 days;
- decision: **BLOCK**;
- calibration claim: `withheld_no_mature_labels`;
- Brier/ECE: **not present and must not be inferred**;
- observation root:
  `sha256:4e10b67a9cc60d0cce822b98c8012399c1e0a33d6656b4838c63cd4d9f20a73b`;
- recommendation receipt ID:
  `sha256:281f7e84dbc56bd1fd75b85a7fae0e2ec2b55953b1f59ade59fd0c6b51b44cf7`.

`receipt-current.json` contains no signature or signer identity. The local
environment had no configured `TRUSTFORGE_SHADOW_DB_PATH`, no canonical
promotion ledger root/keyring was supplied, and no signed current disposition
could therefore be authenticated. The unsigned canary disposition is useful
only as corroborating remain-shadow state; it is not promotion authority.

Future PASS requires an authentic signed current receipt produced from the
canonical observation store, meeting all three elapsed thresholds. Calibration
non-inferiority may be reported only after canonical mature outcomes exist and
the receipt contains independently recomputable Brier/ECE values.

## #872 holder evidence and licensing

No real entity-resolved holder history exists in the checked-in canonical
records. BTC, BNB and ETH all retain `holder_concentration=unknown` with no
numeric value. The authoritative #994 report and accepted issue disposition are
`NO-GO` for procurement and production integration. This satisfies D-5's
requirement to route cost-sensitive research separately and make no purchase,
but D-3 remains shadow/unknown.

Future known status requires licensed, reproducible PIT history with explicit
entity resolution and cross-chain, custodian, bridge, burn, locked-supply and
lost-key treatment. A future trial or purchase requires separate CEO and Harper
authorization; this audit grants none.

## #870 control/governance source-family check

The repository has descriptive BTC records naming two hosts for control and
governance, but it does not have typed, independently attributable and
replayable validator/miner/node/governance planes. Multiple URLs or prose are
not proof of two eligible source families. BNB and ETH control/governance
remain unknown. #1035 is open for the missing typed-plane and canonical
source-withdrawal PIT work.

Current result: **BLOCKED_EXTERNAL / engineering remediation required**. This
supplemental check does not change any of the 16 original external rows.

## #877 real rollback evidence

No tracked real release artifact, signed drill receipt, or release-host evidence
bundle was found. The repository contains the hermetic drill implementation and
tests only. Consequently there is no authentic evidence to validate for:

- immutable A/B release and artifact digests;
- real non-production A→B→regression→A time and rollback SLO;
- signed actor/reason/from/to/config timestamps;
- surviving history and receipts;
- post-rollback A availability and health.

All five external #877 rows remain `BLOCKED_EXTERNAL`. Future evidence must be
an independently verifiable, signed, environment-bound non-production receipt
over real immutable artifacts and include observed elapsed timestamps and
post-rollback health.

## #879 real topology and reconciliation evidence

#879 remains open. #1021 and #1020 provide programmatic provisioning and budget
contracts, but no real release-host evidence proves the complete boundary.
#1019 and P0 evidence issues #1031–#1034 remain open.

No authentic bundle was available that binds signature, release/artifact
digests, collection time, environment, nginx configuration, Linux AF_UNIX peer
identity, direct-spoof behavior, budget ledger heads/reconciliation, two real
handlers, rollback and A health. All K rows therefore remain
`BLOCKED_EXTERNAL`.

Future evidence must pass the independent #1031–#1034 chain and prove the
ordered nginx → AF_UNIX → router → two real Handler path on the named
non-production release host. Synthetic, fixture, temporary-handler,
platform-skipped or signer-self-asserted output is not acceptable.

## Criterion disposition

| Criterion | Result | Current evidence or blocker |
|---|---|---|
| D-3 | `REMAIN_SHADOW` | No entity-resolved holder history; all real holder dimensions remain unknown/no numeric value |
| D-5 | `PASS` | Separate cost-sensitive #994 completed with NO-GO; no purchase or integration occurred |
| F-7 | `REMAIN_SHADOW` | Current real coverage is 3 assets, 3 observations, 2 days and 60% missingness |
| G-1 | `REMAIN_SHADOW` | 1 eligible observation / 3 assets / 2 days is below 200 / 5 / 30 |
| G-5 | `REMAIN_SHADOW` | `labels_mature=false`; no Brier/ECE claim exists |
| I-7 | `BLOCKED_EXTERNAL` | No actual-branch Eye artifact for both locales, desktop/mobile, 200% zoom, long provenance, overflow and error states |
| J-1 | `BLOCKED_EXTERNAL` | No real immutable A/B release artifacts or authenticated digests |
| J-4 | `BLOCKED_EXTERNAL` | No real non-production A→B→regression→A elapsed drill |
| J-5 | `BLOCKED_EXTERNAL` | No signed real actor/reason/release/artifact/config/time receipt |
| J-6 | `BLOCKED_EXTERNAL` | No real post-rollback A availability and health evidence |
| J-7 | `BLOCKED_EXTERNAL` | No real evidence that history and receipts survive rollback |
| K-1 | `BLOCKED_EXTERNAL` | No authenticated nginx/AF_UNIX peer-identity release-host evidence |
| K-2 | `BLOCKED_EXTERNAL` | No independent root-owned atomic install/rollback evidence bundle |
| K-3 | `BLOCKED_EXTERNAL` | No real HTTP unauthorized/duplicate/unknown cost-mode transcript |
| K-4 | `BLOCKED_EXTERNAL` | No signed per-ramp restart reconciliation from the release environment |
| K-5 | `BLOCKED_EXTERNAL` | No real two-release ingress, failure and 100% A recovery evidence |

## Final boundary

Phase 4 reconciliation is complete as an audit snapshot. It does not make the
product release-ready. #748 and #998 must remain open while programmatic
remediation, actual Eye verification, elapsed observations and authentic
release-host evidence are outstanding.
