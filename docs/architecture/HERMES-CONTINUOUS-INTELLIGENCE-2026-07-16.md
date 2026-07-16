# Hermes Continuous Intelligence

## Product contract

Hermes continuously analyzes immutable data snapshots. The user changes what is
being viewed or adds a question; the browser never owns the analysis lifecycle.

```text
crawler update -> immutable SQLite snapshot -> coin × mode × active-question matrix
  -> five overlapping workers -> atomic published result -> read-only UI
```

The five workers are source ingestion, claim extraction, trust reasoning,
Evidence assembly, and report delivery. A package can enter stage 1 while an
older package is in stages 2–5. Retry state, dead letters, duration, queue depth,
question, coin, mode, run and snapshot lineage are durable.

## Question RAG and dialogue

`analysis_questions` remains the source of active analysis intent.
`analysis_conversation` records user questions and published Hermes answers.
The retrieval endpoint ranks mixed Chinese/English questions using deterministic
character bigram and token overlap, with same-coin and same-mode boosts. Matches
retain their completed answer, `snapshot_id`, `job_id`, and publication time.

Retrieval happens inside source ingestion and is written to the execution log as
`retrieval.question_memory`. Historical conclusions are explicitly
**non-evidentiary**: they help expose continuity and prior coverage, but never
become current market Evidence or alter deterministic Trust scoring.

## Interface ownership

- Left rail: Hermes dialogue, current question/mode, recent conversation memory,
  and clickable similar historical questions.
- Center: the selected read-only published snapshot and deep workspaces.
- Right rail: current trust state, component evidence, divergence, and live
  continuous-engine load/queue state.
- Bottom rail: five-stage package execution with the active coin, question,
  snapshot, duration, retry and queue telemetry.

## Outer-framework improvement

Historical pipeline jobs and stage measurements now feed
`diagnose_hermes.py`. The diagnostic may propose orchestration reliability or
question-retrieval diversification experiments. It cannot modify production.
Every change remains:

```text
observe -> propose -> sandbox/replay -> human approval -> versioned activation
```

Trust weights, completed reports, models, production code and deployment remain
outside autonomous mutation.

### Flagship upgrade control plane

The flagship is a metaphor only. The actual WebUI is an upgrade topology, not a
literal six-hardpoint ship or a gamified level counter. `GET
/api/hermes-upgrades` currently inventories 15 versioned modules across Data
Plane, Intelligence, Trust Kernel, Delivery, and Operations. It includes the
packaged core, five immutable outer-skill families, release artifacts and model
gates. The UI shows actual content revision/hash, baseline or approved origin,
append-only change history, diagnostic sandbox candidates and each module's
upgrade channel.

The activation path is `diagnose -> sandbox -> validation -> human approval ->
active pointer -> rollback`. The Trust core is packaged and versioned so it can
be upgraded through a normal reviewed branch/release, but outer artifacts
cannot override it. Recursive self-upgrade and automatic deployment are
explicitly disabled.

## Remaining external gates

- Production connector reliability needs seven consecutive successful observed
  cycles for affected sources.
- Historical raw-source backfill still requires licensed time-stamped archives.
- Calibrator/model work remains gated on leakage-safe historical outcomes.
- Desktop/mobile production screenshot evidence remains required when browser
  automation is available.
