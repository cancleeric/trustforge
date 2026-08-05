# Agent Platform Extraction Feasibility Assessment

> Date: 2026-07-22
> Scope: `agent-platform-kit`, `trustforge-core`, and `trustforge-app` boundary
> Baseline: TrustForge v0.17.2 (`27fe4c5`)
> Status: architecture direction recommended; extraction not implemented
> Update: 2026-08-05 adds Gyre as the second real consumer and maps the
> extraction target to Brain Cloud shared runtime gates.

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

Gyre is now the second real consumer candidate. That changes the extraction
standard: reusable pieces may move toward Brain Cloud only when they serve both
TrustForge market research and Gyre social/operations workflows without carrying
either product's domain semantics.

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

### `brain-cloud-runtime`

`agent-platform-kit` may become the Brain Cloud shared runtime after the
in-monorepo gates pass. Brain Cloud may own generic mechanisms:

- Session, plan, task, and memory-loop orchestration.
- MCP client wiring, tool registry, and skill registry mechanics.
- Provider and runtime resolution, including null/fake contract adapters.
- Approval, policy, budget, security, and idempotency gate interfaces.
- Execution event, telemetry, upgrade review, and rollback primitives.
- Compatibility fixtures that prove both TrustForge and Gyre can consume the
  same generic contracts.

Brain Cloud must not own TrustForge market semantics, Gyre social/operations
semantics, product prompts, product report schemas, product source connectors,
or production deployment authority.

## Second-consumer contract proof

The extraction proof is no longer "TrustForge can be split into neat
directories." The proof is that TrustForge and Gyre can both consume the same
runtime mechanism while keeping their product contracts separate.

| Contract area | Generic Brain Cloud contract | TrustForge-owned contract | Gyre-owned contract |
|---|---|---|---|
| Runtime entry | session/task execution envelope, cancellation, retry, trace IDs | market analysis request, run scope, Trust Kernel handoff | social/operations workflow request and campaign/task state |
| Provider wiring | `resolve_providers()` style registry and selected provider identity | allowed model/provider policy for TrustForge runs | allowed model/provider policy for Gyre workflows |
| Tools and skills | registry lifecycle, capability metadata, approval hooks | crypto/market connectors and TrustForge skill families | Gyre channel, content, social, and operations tools |
| Events and telemetry | event schema, severity, timing, correlation IDs | Hermes/TrustForge projector and report lineage | Gyre projector and operations dashboard lineage |
| Policy and rollback | generic stage/approve/activate/rollback state machine | TrustForge module catalog and release authority | Gyre module catalog and release authority |

The gate passes only when the shared contract can be exercised by both
consumers without adding `coin`, `stance`, `Evidence`, `Report`, Hermes node
names, Gyre campaign semantics, or social-platform terms to the generic layer.

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

## Incremental migration rule

Extraction must stay incremental and reversible:

1. Enforce import boundaries before moving files.
2. Route production provider/runtime wiring through the resolver before adding
   more adapters.
3. Remove domain-shaped fields from generic contracts before publishing any
   Brain Cloud package.
4. Make `run_kernel()` the only TrustForge application-to-core entry before
   moving trust-domain computation.
5. Add Gyre as a contract-proof consumer before claiming a shared runtime API.
6. Keep versioning, compatibility, and rollback plans in place before any
   package move or repository split.

This maps to the open phase issues:

| Phase | Issue | Gate | Acceptance focus |
|---|---|---|---|
| Import boundaries | #1445 | G1 | `kit`, `core`, and `app` imports are enforced |
| Provider/runtime wiring | #1446 | G2 | production wiring uses provider/runtime resolution |
| Domain decoupling | #1447 | G3 | generic contracts have no TrustForge or Gyre domain leakage |
| `run_kernel()` exclusive entry | #1442 | G4 | TrustForge deterministic core is accessed through golden-vector gates |
| Brain Cloud adapter + Gyre proof | #1443 | G4+G5 | shared runtime contract is consumed by TrustForge and Gyre |
| Versioning + rollback | #1444 | G6 | compatibility, release, and rollback rules are documented |

## Main risks

1. **Premature abstraction.** Gyre is a second consumer candidate, but it is not
   a license to publish early. Publishing before the Gyre proof could still
   freeze one product's APIs as generic contracts.
2. **Directory-only separation.** Moving files without changing production
   imports would create three names but retain one coupled system.
3. **Semantic leakage.** Coin, stance, Evidence, Hermes, and trust-policy terms
   can silently enter the reusable package.
4. **Behaviour drift.** Moving scoring code can change deterministic results
   unless golden vectors compare old and new outputs exactly.
5. **Operational regression.** Budget, security, idempotency, and rollback
   controls must remain fail-closed during migration.
6. **Versioning overhead.** Splitting repositories before the Gyre proof and
   rollback gate pass adds release coordination without proving reuse.

## Feasibility decision

Proceed under these conditions:

1. Build three import boundaries in the current monorepo first.
2. Connect the production path to provider resolution before implementing more
   adapters.
3. Make `run_kernel()` the exclusive application-to-core entry point.
4. Preserve exact TrustForge outputs through golden and regression tests.
5. Keep GitHub Actions production deployment disabled; deployment remains a
   separate, explicitly authorized release operation.
6. Publish or move `agent-platform-kit` only after Gyre proves the API as a
   second real consumer and the compatibility/rollback gate passes.

Estimated delivery size is **12-22 focused PRs** across provider/runtime,
platform governance, kernel separation, and application composition.

## Key takeaways

- The target architecture is sound and already has useful seams.
- AgentCore is an adapter, not the TrustForge core.
- Bedrock is a model provider; AgentCore may be a broader runtime.
- The reusable package should contain mechanisms, not TrustForge policies.
- `trustforge-core` must be pure and independently testable.
- The production wiring must change before the separation can be called real.
