# TrustForge UX Execution Journey Refresh

Date: 2026-07-13
Status: release candidate ready; production data-state QA pending

## Product Intent

TrustForge is an operational Hermes Agent console, not a marketing landing page.
Every screen must make a reviewer answer five questions without hunting:

1. What is the current market snapshot?
2. Which sources are available, fresh, stale, or missing?
3. What did Hermes execute, in what order, and how long did each node take?
4. Which evidence supports or challenges the conclusion?
5. Which metric is a trust score and which is information completeness?

## Design Direction

Borrow the useful idea from the Scroll World skill: one continuous, coherent journey.
For TrustForge this is a data journey, not a cinematic scroll video:

`snapshot -> sources -> agent execution -> evidence -> conclusion -> audit trail`

The interface stays compact and work-focused. No decorative 3D world, autoplay media, or
marketing card stacks. Visual emphasis comes from data hierarchy, connected execution
nodes, source state, and evidence traceability.

## Confirmed UX Defects

- The home screen reads as an introduction page instead of the daily control surface.
- Raw trust score and calibrated information completeness occupy similar visual weight.
- The analysis gauge previously showed a percentage without a visible metric name.
  This was patched in v0.13.9; the refresh must preserve that clarity everywhere.
- The result page shows data panels but does not lead the eye through the Hermes workflow.
- Source freshness, availability, and per-source provenance are not first-class in the
  analysis journey.

## Scope

### 1. Overview

- Replace explanatory hero dominance with a compact live snapshot band.
- Promote coin cards into a scan-friendly market matrix with explicit `信任分` and
  `資訊完整度` labels, source freshness, and an unambiguous next action.
- Move product explanation into a quiet secondary location.

### 2. Analysis

- Establish a stable two-column work surface: request controls on the left and results on
  the right on desktop; a single vertical flow on mobile.
- Make the execution journey the primary result structure: source collection, validation,
  inference, evidence binding, and report output.
- Give every node a state, duration, input/output count, and drill-down evidence link.

### 3. Evidence and Sources

- Show source kind, publisher, `published_at`, `fetched_at`, freshness, result state, and
  whether the source was used or excluded.
- Clearly separate `fresh`, `stale`, `missing`, and `degraded`; never present missing data
  as a neutral fact.

### 4. Supporting Pages

- Align comparison, history, status, and cost pages to the same metric vocabulary,
  table density, filters, empty states, and error states.
- Retain all existing audit data and full cost-ledger pagination.

## Non-Negotiable Acceptance Criteria

- A BTC overview card and its analysis report show the same named metric value for the
  same snapshot; different snapshots display their timestamp visibly.
- No percentage appears without a visible metric name.
- A reviewer can trace one conclusion to source and execution node without reading raw
  JSON.
- Desktop and mobile screenshots have no overlap, clipping, or horizontal overflow.
- Existing API contracts, execution logs, evidence binding, cost history, and source
  timing fields remain intact.
- Release contains the complete UX set, targeted component tests, build, local server
  smoke, GitHub QA, and post-deploy production smoke.

## Delivered In This Refresh

### Round 1: operational information architecture

- Replaced the landing-style home hero with a market snapshot, source-cache health, and
  a direct path into a fresh Hermes run.
- Made home cards show both `信任分` and `資訊完整度` as fixed, named values, with the
  snapshot time visible.
- Reframed analyze, compare, history, status, and cost pages as compact operational
  surfaces; the cost ledger remains append-only and paginated rather than truncated.

### Round 2: metric semantics and auditability

- Made the report gauge always represent `資訊完整度（校準後）`. `信任分` remains a
  separate, named value so a changing decision state cannot silently change the meaning
  of the same visual.
- Moved the Hermes execution journey directly beneath the conclusion. Each node exposes
  its original event count, observed duration, and real success/failure state; source
  details retain documents and network duration.
- Humanized report and ledger timestamps while preserving the raw ISO timestamp in a
  hover title for audit use.

### Round 3: release-candidate checks

- Checked desktop route layouts for main-content horizontal overflow.
- Verified frontend tests (`24` files / `244` tests), production build, backend tests
  (`2048 passed`, `6 skipped`, `92.42%` coverage), and whitespace-safe diffs locally.
- The isolated local browser cannot reach the local API proxy, so data-state browser
  screenshots remain a release/production gate rather than a claimed local pass.

## Remaining Release Gate

Run production browser smoke with a real completed run: open overview, run analysis,
verify execution nodes/source table/log download, retry a conflict response, paginate
the cost ledger, and capture desktop plus mobile evidence. This is also the remaining
acceptance step for H-17 and H-21.
