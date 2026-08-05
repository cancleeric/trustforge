# Agent Platform Extraction — Development Plan

> Derived from [`AGENT-PLATFORM-EXTRACTION-FEASIBILITY-2026-07-22.md`](./AGENT-PLATFORM-EXTRACTION-FEASIBILITY-2026-07-22.md)
> Tracking: #1440 | Target: Brain Cloud shared runtime | Second consumer: Gyre
> Estimated: 12-22 PRs across 6 phases

## Architecture target

```
Brain Cloud Hermes Runtime (shared)
  ├─ generic session/plan/task/memory/tool loop
  ├─ MCP client + tool/skill registries
  ├─ approval/policy/budget/security/idempotency gates
  └─ execution events/telemetry/upgrade review
        │
   ┌────┴────┐
   │         │
TrustForge  Gyre
market      social
research    operations
```

Three-package boundary (in-monorepo first, move to Brain Cloud only after all gates pass):

```
trustforge-app ──depends on──> agent-platform-kit
                            └─depends on──> trustforge-core
agent-platform-kit ──must not depend on──> trustforge-core
```

## Phase breakdown (6 issues, mapped to 6 extraction gates)

| Phase | Issue | Gate | Goal | Est. PRs |
|-------|-------|------|------|----------|
| 1 | Import boundary | G1 | Three real+enforced import layers | 2-3 |
| 2 | Provider/runtime wiring | G2 | Production uses resolve_providers(), not direct Bedrock | 3-4 |
| 3 | Domain decoupling | G3 | Generic contracts carry no coin/stance/Hermes semantics | 3-5 |
| 4 | Kernel exclusive + golden | G4 (TF side) | run_kernel() exclusive entry, golden vectors pass | 2-3 |
| 5 | Brain Cloud adapter + Gyre proof | G4+G5 | Both consumers pass same contract suite | 2-4 |
| 6 | Versioning + rollback | G6 | Versioned compatibility + rollback plan | 1-3 |

## Hard rules (from feasibility assessment)

1. Build boundaries **in current monorepo first** — no premature repo split.
2. Connect production path to provider resolution **before** more adapters.
3. `run_kernel()` is the **exclusive** application-to-core entry.
4. Preserve exact TrustForge outputs via golden + regression tests.
5. GitHub Actions production deployment stays **disabled**.
6. Publish/move `agent-platform-kit` only after Gyre proves the API.

## What stays in TrustForge (never moves)

- Five-node market-research workflow + node projection
- Coin/stance/Document/Evidence/Report/financial-direction semantics
- Source connectors, reputation, corroboration, manipulation rules
- PIT snapshots, OHLCV replay, calibration, abstention
- TrustForge skill families, policy schemas, prompts, UI, schedules
- TrustForge production authority + cost controls
