# TrustForge Finale Two-Week Execution Plan

Date: 2026-07-18
Deadline: 2026-08-01
GitHub milestone: `Finale 2026-08-01`
Canonical control issue: #220

## Delivery Definition

The two-week target is a truthful, production-ready and demonstrable TrustForge
workflow. It is not a deadline to ship speculative calibration models or an
unfinished data lake.

Required final state:

1. Manual analysis produces a durable, visible result without waiting for a
   background cadence.
2. Hermes scheduled work runs every 30 minutes, can be started and stopped,
   and has auditable run evidence.
3. Formal Bedrock and online-source behaviour are evidenced, including honest
   degradation when a provider is unavailable.
4. Desktop/mobile demo evidence, recording and submission security evidence
   are complete.

## Critical Path

```text
#219 priority manual jobs --> #221 scheduler acceptance --+
                                                     +--> #204 demo evidence
#202 Bedrock smoke --> #203 online QA --> #206 demo hardening --+

#199 HOYA honest status ------------------------------+
#104 + #113 production ops evidence ------------------+
#205 submission security gate -------------------------+

#167 HOYA real connector is an external enhancement, not a blocker for #204.
```

## Ordered Work

| Order | Issue | Execution Stream | Dependency | Exit Evidence |
| --- | --- | --- | --- | --- |
| 1 | #219 | Runtime | none | Manual and scheduled jobs share durable priority contract; manual worker wake-up and dedup tests. |
| 2 | #221 | Runtime | #219 for final priority assertion | Three production cadence records; start/stop propagation; scheduler telemetry. |
| 3 | #202 | Release evidence | #170 or recorded policy assumption | One non-offline BTC artifact with model, cost and no placeholder. |
| 4 | #203 | Release evidence | #202 | Five coins x three modes online matrix, p95 and explicit source degradation. |
| 5 | #199 | Product truthfulness | none | HOYA status is truthful when contract/env is absent; startup self-check and tests. |
| 6 | #206 | Product truthfulness | #202 state contract | No traceback, raw network failure or offline placeholder in demo surface. |
| 7 | #104, #113 | Operations evidence | production access | Alarm, trusted client-IP and admin alerting evidence. |
| 8 | #204 | Release evidence | 1-7 | Desktop/mobile captures and full recorded run at a release tag. |
| 9 | #205 | Submission safety | human public/private decision | Secret/internal-reference scan and submission checklist. |

## Parallel Lanes

### Lane A: Runtime

- #219 then #221.
- One developer owns job priority, worker wake-up, scheduling semantics and
  telemetry. Do not mix frontend design work into these pull requests.

### Lane B: Trustful Presentation

- #199 and #206 can run in parallel with Lane A.
- #199 never fabricates a HOYA connection. #206 makes every unavailable or
  degraded state explicit and demo-safe.

### Lane C: Release Evidence

- #202 then #203, followed by #204.
- This lane owns paid calls and must keep Bedrock enabled only for the bounded
  smoke/matrix window under the configured daily cap.

### Lane D: Security and Submission

- #104, #113 and #205 run independently of feature work.
- Human decisions/credentials are recorded as external blockers, not worked
  around with fixtures or copied secrets.

## Deferred Work

Issues #195, #196, #197 and #198 remain valuable but are not release blockers.
They depend on genuine heterogeneous historical coverage and leakage-safe,
eligible outcomes. #8 and #167 are external connector dependencies. They must
not be represented as completed until their real credentials/contracts exist.

## Execution Rules

1. Issues remain unassigned; we jointly work them in this fixed order.
2. Every pull request references its issue and states dependency impact.
3. Runtime changes require backend tests plus a production-safe smoke plan.
4. Security and cost changes require adversarial review before merge.
5. #204 is the final integration gate; no feature branch merges directly into
   the demo capture window without release-owner approval.
