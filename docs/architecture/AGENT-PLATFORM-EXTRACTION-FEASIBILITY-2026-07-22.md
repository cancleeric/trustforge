# Agent Platform Extraction Feasibility Assessment

> Date: 2026-07-22
> Scope: `agent-platform-kit`, `trustforge-core`, and `trustforge-app` boundary
> Baseline: TrustForge v0.17.2 (`27fe4c5`)
> Status: architecture direction recommended; extraction not implemented

## Executive summary

The proposed three-package architecture is feasible and is the recommended
long-term direction. Overall feasibility is assessed as **8/10**, provided the
work is performed incrementally inside the current repository before any
package is published or moved to another repository.

The required dependency direction is:

```text
trustforge-app
   |-- depends on agent-platform-kit
   `-- depends on trustforge-core

agent-platform-kit -- must not depend on --> trustforge-core
trustforge-core     -- must not depend on --> agent-platform-kit
```

`trustforge-app` is the composition root. It is the only layer allowed to know
about both the reusable agent platform and the TrustForge trust domain.

AgentCore should be treated as a replaceable agent runtime adapter. Bedrock is
primarily a model provider. Neither belongs in the TrustForge deterministic
core.

## Why this is feasible

The repository already contains the beginnings of the intended seams:

- `ports.py` defines runtime-checkable LLM, cache, source, observability, and
  budget protocols.
- Bedrock, AgentCore, SQLite, null, and fake adapter positions exist.
- `resolve_providers()` records configured and resolved provider identities.
- Policy artifacts have stage, approval, activation, and rollback concepts.
- Upgrade proposals and review results have a durable queue.
- Module telemetry records lifecycle and invocation evidence.
- `trust/kernel.py` defines versioned kernel input/output contracts.

These are useful structural assets, but they do not yet prove runtime
decoupling. In the current production path:

- The orchestrator still imports and constructs `BedrockClient` directly.
- The production pipeline does not call `resolve_providers()`.
- `AgentCoreLLMAdapter` raises `NotImplementedError` for both operations.
- The orchestrator imports `trust.scoring` directly instead of using
  `run_kernel()` as the exclusive core entry point.
- Policy resolution is logged, but resolved values do not consistently drive
  runtime consumers.
- Formal module telemetry instrumentation covers only part of the pipeline.

The architecture therefore has **prepared interfaces but incomplete wiring**.

## Recommended package responsibilities

### `agent-platform-kit`

Own generic agent operations, governance primitives, and provider integration:

- Model and agent-runtime ports
- Provider registry and dependency injection
- Null/fake contract-test adapters
- Generic policy artifact lifecycle
- Generic skill registry engine
- Module telemetry engine
- Upgrade proposal state machine and review workflow
- Generic execution event log and lineage primitives
- Budget reservation and accounting interfaces
- Security, authorization, and idempotency gate interfaces

It must not contain TrustForge coins, Evidence/Report schemas, trust weights,
Hermes workflow nodes, financial direction rules, or deployment authority.

### `trustforge-core`

Own deterministic, reproducible trust-domain computation:

- Versioned kernel input/output contracts
- Claim scoring and corroboration
- Source reputation
- Dawid-Skene aggregation
- Confidence and deterministic abstention logic
- Deterministic manipulation/collusion signals
- Pure domain types and golden test vectors

It must not import AWS SDKs, HTTP clients, databases, caches, agent runtimes,
LLMs, skills, Web/API code, deployment code, or `agent-platform-kit`.

### `trustforge-app`

Own product assembly and TrustForge-specific workflows:

- Pipeline and Hermes orchestration
- API, Web UI, CLI, and deployment assembly
- Crypto and financial connectors
- TrustForge Document, Evidence, and Report contracts
- Prompt templates and stance-task semantics
- Financial direction and market-manipulation interpretation
- Calibration jobs and market outcomes
- TrustForge skill families and policy schemas
- TrustForge module catalog and operator views
- Adapter selection and dependency construction

## Important boundary corrections

### Separate agent runtime from model provider

AgentCore and Bedrock should not be forced into one oversized interface.

```text
AgentRuntime                  ModelProvider
  run()                         complete()
  open_session()                structured_output()
  invoke_tool()                 usage()
  emit_trace()

AgentCoreRuntime              BedrockModelProvider
                              LocalModelProvider
                              OpenAICompatibleModelProvider
```

TrustForge should depend on the smallest interface needed by each use case.
Most current LLM work needs a `ModelProvider`; only session/tool/runtime use
cases should require `AgentRuntime`.

### Do not extract domain-shaped ports unchanged

Several apparently generic components still carry product semantics:

| Current component | Embedded product coupling | Required change |
|---|---|---|
| `LLMProvider.classify_stance()` | TrustForge stance vocabulary | Keep stance classifier in app/domain; leave generic completion in the kit |
| `SourceProvider.fetch(query, coin)` | Coin and TrustForge Document schema | Define source/tool ports in the app or use a generic request/result envelope |
| `ExecutionLog` | Hermes run IDs, nodes, and event mapping | Split generic event log from a TrustForge/Hermes projector |
| Skill registry | Fixed source/analysis/report/evaluation/improvement families | Inject family catalog and schema from the app |
| Upgrade control | TrustForge module IDs and repository paths | Keep state machine in kit and inject the module catalog |
| Budget guard | Bedrock pricing and TrustForge request modes | Separate generic quota reservation from provider pricing adapters |
| Security gate | Product-specific Web/API rules | Keep generic decision contract in kit and rules in app |

## Current separation scorecard

| Boundary | Current state | Assessment |
|---|---|---|
| Provider protocols | Implemented | Useful foundation |
| Bedrock adapter | Implemented, but bypassed by production orchestration | Partial |
| AgentCore adapter | Declared, operations unimplemented | Not usable |
| Provider resolver | Implemented, not used by production pipeline | Not connected |
| Trust Kernel contract | Implemented | Partial boundary |
| Kernel as exclusive production entry | Not implemented | Blocking full separation |
| Policy lifecycle | Implemented | Coupled to TrustForge families |
| Runtime policy consumption | Incomplete | Partial |
| Module telemetry engine | Implemented | Partially instrumented |
| Upgrade queue/review | Implemented | Reusable after catalog decoupling |
| Execution log | Implemented | Hermes-specific |
| Independent packages | Not present | Planned |

## Main risks

1. **Premature abstraction.** TrustForge is currently the only confirmed
   consumer. Publishing too early could freeze domain-specific APIs as generic
   contracts.
2. **Directory-only separation.** Moving files without changing production
   imports would create three names but retain one coupled system.
3. **Semantic leakage.** Coin, stance, Evidence, Hermes, and trust-policy terms
   can silently enter the reusable package.
4. **Behaviour drift.** Moving scoring code can change deterministic results
   unless golden vectors compare old and new outputs exactly.
5. **Operational regression.** Budget, security, idempotency, and rollback
   controls must remain fail-closed during migration.
6. **Versioning overhead.** Splitting repositories before a second consumer
   exists adds release coordination without proving reuse.

## Feasibility decision

Proceed under these conditions:

1. Build three import boundaries in the current monorepo first.
2. Connect the production path to provider resolution before implementing more
   adapters.
3. Make `run_kernel()` the exclusive application-to-core entry point.
4. Preserve exact TrustForge outputs through golden and regression tests.
5. Keep GitHub Actions production deployment disabled; deployment remains a
   separate, explicitly authorized release operation.
6. Publish or move `agent-platform-kit` only after a second real project proves
   the API.

Estimated delivery size is **12-22 focused PRs** across provider/runtime,
platform governance, kernel separation, and application composition.

## Key takeaways

- The target architecture is sound and already has useful seams.
- AgentCore is an adapter, not the TrustForge core.
- Bedrock is a model provider; AgentCore may be a broader runtime.
- The reusable package should contain mechanisms, not TrustForge policies.
- `trustforge-core` must be pure and independently testable.
- The production wiring must change before the separation can be called real.
