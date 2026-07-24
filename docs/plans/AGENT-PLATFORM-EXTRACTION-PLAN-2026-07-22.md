# Agent Platform Extraction Development Plan

> Date: 2026-07-22
> Input: [Agent Platform Extraction Feasibility Assessment](../architecture/AGENT-PLATFORM-EXTRACTION-FEASIBILITY-2026-07-22.md)
> Status: proposed; implementation has not started
> Delivery model: issue -> scoped branch -> PR -> CI -> adversarial review -> merge
> Production deployment: out of scope; GitHub Actions production deployment remains disabled

## Objective

Create enforceable package boundaries for reusable agent operations,
deterministic TrustForge algorithms, and application assembly without changing
TrustForge's externally observable analysis results.

Target layout:

```text
src/
|-- agent_platform_kit/
|-- trustforge_core/
`-- trustforge_app/
```

The first delivery stays in the current repository. Independent publishing is
a later decision requiring a second consuming project.

## Global acceptance criteria

- `trustforge_core` imports no network, database, cloud, LLM, skill, UI, or
  deployment modules.
- `agent_platform_kit` imports no TrustForge domain or application module.
- `trustforge_app` is the sole composition root.
- Production orchestration receives providers through explicit construction or
  injection; it does not construct `BedrockClient` internally.
- AgentCore can be enabled as an adapter without changing core code.
- Production trust computation enters through the versioned kernel contract.
- Existing API contracts and deterministic analysis outputs remain compatible.
- Security, cost, idempotency, approval, and rollback gates remain fail-closed.
- Every migration PR includes regression tests and an import-boundary check.
- No issue in this plan enables or invokes production deployment.

## Workstream dependency map

```text
W0 Boundary guardrails
 |-- W1 Provider and runtime contracts -- W2 Production provider wiring
 |                                      `-- W3 AgentCore adapter
 |-- W4 Generic execution/telemetry
 |-- W5 Governance primitives
 `-- W6 Kernel physical separation ------ W7 App composition migration
                                           `-- W8 Packaging decision
```

W0 is the prerequisite. W1, W4, W5, and the preparation portion of W6 can
proceed independently. W2 must precede W3. W6 must be stable before W7 removes
legacy imports.

## Phase 0 - Baseline and boundary enforcement

### Issue W0.1: Capture behavioural baselines

Deliverables:

- Golden kernel input/output fixtures for representative trust scenarios.
- API response compatibility fixtures for analysis and comparison paths.
- Offline, Bedrock-stub, failure, and budget-exhaustion fixtures.
- Recorded test commands and baseline results.

Acceptance criteria:

- Fixtures cover support, contradiction, abstention, sparse sources, repeated
  sources, and manipulation signals.
- Tests compare structured values rather than presentation-only snapshots.
- No live provider call or production deployment is required.

### Issue W0.2: Add import-boundary tests

Deliverables:

- Automated checks for forbidden dependency directions.
- Documented allowlist for temporary migration bridges.
- A removal date or successor issue for every temporary bridge.

Acceptance criteria:

- A deliberate forbidden import makes the test fail.
- `trustforge_core -> agent_platform_kit` and
  `agent_platform_kit -> trustforge_*` are rejected.

## Phase 1 - Provider and runtime seam

### Issue W1.1: Define minimal provider contracts

Deliverables:

- `ModelProvider` with completion, structured response, usage, and provider
  identity contracts.
- `AgentRuntime` with session, run, tool invocation, and trace contracts.
- Typed error taxonomy and capability discovery.
- Shared contract tests for null and fake implementations.

Acceptance criteria:

- Stance, coin, Evidence, and Hermes terms are absent from generic contracts.
- Callers depend only on the smallest required protocol.
- Provider result includes enough usage data for budget accounting.

### Issue W1.2: Move Bedrock behind `ModelProvider`

Deliverables:

- `BedrockModelProvider` adapter.
- Mapping from Bedrock errors and token usage to platform contracts.
- Contract tests using stubs/fakes.

Acceptance criteria:

- Adapter-specific AWS types do not cross the port boundary.
- Existing offline and cost-recording behaviour remains compatible.

### Issue W2.1: Wire provider resolution into production orchestration

Deliverables:

- Application composition root that constructs a provider set.
- Orchestrator functions accept provider protocols.
- Removal of internal `BedrockClient()` construction from formal run paths.
- Provider selection and fallback recorded in execution evidence.

Acceptance criteria:

- Bedrock, null, and fake providers pass the same pipeline contract tests.
- `resolve_providers()` or its successor is exercised by the production entry
  path.
- Unsupported/misconfigured providers fail clearly and safely.

### Issue W3.1: Implement AgentCore runtime adapter

Deliverables:

- AgentCore session/run/tool/trace mapping.
- Optional `ModelProvider` facade only if AgentCore exposes the required model
  operation safely.
- Timeout, cancellation, retry, and usage propagation.
- Adapter contract and integration tests.

Acceptance criteria:

- Enabling AgentCore requires no import or modification in `trustforge_core`.
- AgentCore failure cannot bypass TrustForge budget or security gates.
- No credentials or deployment authority enter generic execution events.

## Phase 2 - Generic execution and observability

### Issue W4.1: Split generic execution log from Hermes projection

Deliverables:

- Generic run/event/step records in `agent_platform_kit`.
- TrustForge/Hermes node mapping in `trustforge_app`.
- Compatibility serializer for existing JSONL and API consumers.

Acceptance criteria:

- Generic event code contains no Hermes node names.
- Existing TrustForge execution manifests remain compatible.
- Lineage supports provider, policy revision, tool invocation, cost, and result
  references without embedding secrets.

### Issue W4.2: Extract telemetry engine and complete instrumentation

Deliverables:

- Generic telemetry store protocol and SQLite adapter.
- Explicit lifecycle transition rules.
- Instrumentation at provider resolution, provider invocation, kernel run,
  policy resolution, and upgrade actions.

Acceptance criteria:

- Registered, configured, resolved, invoked, and verified have evidence-backed
  meanings.
- Queue overflow and storage failures are observable and do not corrupt the
  trust result.
- Tests cover concurrency and graceful shutdown.

## Phase 3 - Governance primitives

### Issue W5.1: Generalize skill and policy artifact lifecycle

Deliverables:

- Generic immutable artifact store and revision pointer.
- Stage, approve, activate, and rollback services.
- Application-injected family catalog and validators.
- TrustForge family definitions retained in `trustforge_app`.

Acceptance criteria:

- The platform package does not know the five Hermes families.
- Staged artifacts never affect formal runs before human approval.
- Activation and rollback are append-only and auditable.

### Issue W5.2: Generalize upgrade queue and review state machine

Deliverables:

- Generic proposal, review, sandbox, decision, activation, and rollback states.
- Injected module catalog and artifact activation handler.
- TrustForge operator/API projection retained in the app.

Acceptance criteria:

- LLM review can recommend sandbox work but cannot approve, merge, or deploy.
- Approval requires a successful sandbox result and a human actor.
- Terminal and invalid state transitions are rejected.

### Issue W5.3: Extract budget, security, and idempotency primitives

Deliverables:

- Generic quota reservation/commit/release interfaces.
- Provider-specific pricing adapter contract.
- Generic authorization/security decision contract.
- Generic idempotency lease contract with durable and local adapters.

Acceptance criteria:

- TrustForge request modes and Bedrock price tables remain outside generic
  primitives.
- Backend uncertainty preserves current fail-closed cost/security behaviour.
- Concurrency tests prove duplicate expensive work is bounded.

## Phase 4 - Trust Kernel physical separation

### Issue W6.1: Define independent core domain contracts

Deliverables:

- Immutable typed `KernelInput` and `KernelOutput` contracts.
- Pure Claim, scored-claim, source-reputation, and result types.
- Explicit contract versioning and compatibility policy.

Acceptance criteria:

- Types do not import app schemas or ingestion Documents.
- Inputs contain normalized facts required for deterministic computation only.

### Issue W6.2: Move pure algorithms into `trustforge_core`

Deliverables:

- Trust score and corroboration algorithms.
- Dawid-Skene/source reputation logic.
- Deterministic confidence, abstention, and manipulation signals.
- Golden-vector parity tests.

Acceptance criteria:

- Old and new implementations match all approved golden vectors.
- Importing the core causes no filesystem, thread, network, environment, or
  database side effect.
- Core tests run without AWS and application dependencies.

### Issue W6.3: Make `run_kernel()` the exclusive core entry point

Deliverables:

- App mapper from normalized application data to `KernelInput`.
- Result mapper from `KernelOutput` to TrustForge Evidence/Report assembly.
- Removal of application imports of core implementation internals.

Acceptance criteria:

- Production orchestration no longer imports `trust.scoring` directly.
- Import-boundary checks prevent regression.
- API and report compatibility suites pass.

## Phase 5 - Application composition and cleanup

### Issue W7.1: Establish `trustforge_app` composition root

Deliverables:

- Explicit construction of providers, stores, gates, telemetry, policies, and
  kernel gateway.
- Configuration validation at startup.
- Test builders for common runtime modes.

Acceptance criteria:

- Offline, Bedrock, and AgentCore modes differ only in composition/configuration.
- Domain algorithms are not selected through environment variables inside the
  core.

### Issue W7.2: Remove migration bridges and duplicate implementations

Deliverables:

- Removal of deprecated direct imports and compatibility facades.
- Updated module catalog and architecture diagrams.
- Migration notes and operator documentation.

Acceptance criteria:

- Temporary import allowlist is empty.
- No duplicate provider resolver or kernel implementation remains.
- Full tests, lint, build, and `git diff --check` pass.

## Phase 6 - Reuse validation and packaging decision

### Issue W8.1: Validate with a second consumer

Deliverables:

- A small non-TrustForge application using provider, telemetry, execution-log,
  and approval primitives.
- API friction report listing every TrustForge-specific leak found.

Acceptance criteria:

- The consumer does not import `trustforge_core` or `trustforge_app`.
- No generic API change is justified only by TrustForge terminology.

### Issue W8.2: Decide distribution model

Decision options:

- Keep the kit as a monorepo package.
- Publish a versioned package from the monorepo.
- Move it to an independent repository after compatibility and ownership rules
  are defined.

Acceptance criteria:

- Semantic-versioning policy, support window, security ownership, and release
  process are documented.
- The decision does not enable GitHub Actions production deployment for
  TrustForge.

## Verification matrix

| Area | Required verification |
|---|---|
| Import boundaries | Static dependency tests and forbidden-import fixtures |
| Kernel | Unit tests, golden vectors, deterministic replay |
| Providers | Shared contract suite, timeout/error/usage cases |
| AgentCore | Stubbed integration plus explicitly authorized non-production smoke test |
| Execution log | Schema compatibility and secret-redaction tests |
| Telemetry | Concurrency, queue pressure, storage failure, shutdown |
| Governance | State-transition, approval, rollback, tamper tests |
| Budget/security | Fail-closed and multi-instance race tests |
| App | API contract, comparison, offline, desktop/mobile UI eye scan where changed |

## Pull-request sizing and milestones

Expected delivery is **12-22 focused PRs**:

| Milestone | Expected PRs | Exit condition |
|---|---:|---|
| M0 Guardrails | 2-3 | Golden baselines and dependency checks active |
| M1 Replaceable providers | 3-5 | Production uses provider ports; AgentCore adapter tested |
| M2 Platform operations | 3-5 | Generic log, telemetry, and governance seams active |
| M3 Pure Trust Kernel | 3-6 | Production enters core only through versioned contract |
| M4 Composition/cleanup | 1-3 | Legacy bridges removed and docs synchronized |
| M5 Reuse decision | 1-2 | Second consumer evidence and packaging decision recorded |

Report after each milestone or after more than three PRs, whichever occurs
first. Every PR must follow the repository review, CI, adversarial review, and
attestation requirements.

## Clean up

At the end of each phase:

- Remove superseded compatibility paths when downstream callers have migrated.
- Update architecture diagrams and status tables to reflect verified runtime
  wiring, not planned interfaces.
- Archive completed one-time plan material according to `docs/README.md`.
- Keep the active plan limited to unfinished work and link completed issues/PRs.

## What's next

Create the Phase 0 issues first. W0.1 and W0.2 establish the safety net needed
before provider or kernel movement. After those are approved, W1.1 and W6.1 can
begin in parallel as separate scoped changes. Implementation must not begin
without explicit issue acceptance criteria and CEO approval under the current
  development workflow.
