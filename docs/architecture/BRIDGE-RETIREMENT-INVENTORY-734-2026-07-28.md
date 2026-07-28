# #734 Core bridge retirement inventory

Status: implementation checkpoint; production cutover is not yet authorized by
this document.

## Authoritative production boundary

`trustforge.agent.kernel_mapper.run_authoritative_judgment` is the sole
application composition boundary for production judgment. It constructs a
non-null immutable `KernelRunResolution`, calls
`trustforge_core.run_kernel` exactly once, validates the complete output graph,
and projects a `KernelJudgment`. Resolution, kernel, contract, or projection
failure propagates. There is no in-process legacy fallback; recovery is through
release-level A/B rollback.

Production consumers:

- `agent.orchestrator.run_agent_pipeline`
- `analysis_flow.AnalysisFlow._stage_trust_reasoning`

## Retained compatibility facades

| Facade | Consumers | Owner | Contract | Decision |
|---|---|---|---|---|
| `kernel_mapper.to_kernel_input(..., direction=None)` | archived parity and shadow tests/tools | Core platform | Kernel input 2.2.0 | Compatibility only. Production boundary always supplies an exact `ResolvedDirection` and asserts non-null resolution. Remove with archived #732 utilities after release rollback retention expires. |
| `kernel_mapper.to_legacy_scoring` | parity/golden tests | Core platform | Kernel output 2.2.0 → app DTO shape | Retain as a named projection-only wrapper. It cannot score or aggregate. Remove when all presentation consumers accept `KernelJudgment` directly. |
| `trustforge.trust.scoring.score` / `aggregate` | offline research, legacy unit tests | Research platform | legacy app DTO | Forbidden from production entrypoints. Retain for research comparison until release B is verified and the rollback-retention window closes. |
| `agent.shadow_runtime.observe_candidate` | retired #732 candidate tooling | Core platform | reviewed #732 candidate digest | Retired and disconnected from production. The old attestation digest intentionally fails closed after #734 adapter changes; do not re-sign it. Delete after audit retention. |
| `trustforge.kernel_runner.run_kernel` | compatibility tests/tools | Core platform | pre-core stub | Forbidden from production call graph. Delete in the final retirement PR after consumer inventory reaches zero. |
| `trustforge.trust.kernel.run_kernel` | legacy compatibility tests/tools | Core platform | legacy facade | Forbidden from production call graph. Delete in the final retirement PR after consumer inventory reaches zero. |

## Enforced evidence

- AST regression prohibits direct `score()` or `aggregate()` calls in both
  production entrypoints.
- Call-count regression requires exactly one canonical kernel call and a
  non-null resolution.
- Injected kernel failure must propagate without invoking a legacy producer.
- Full local pre-push gate passed on the working tree before this inventory
  refinement; it must be rerun against the final commit.
- Eye reported high blast radius, so full-suite and post-merge verification are
  mandatory. No UI files changed; visual behavior is out of scope.
