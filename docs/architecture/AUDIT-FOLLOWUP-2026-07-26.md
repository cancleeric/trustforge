# Architecture Audit Follow-up (#380)

> Date: 2026-07-26
> Based on: v0.24.0 codebase

## 外框升級窗口

### SageMaker Training Backend
- `src/trustforge/sagemaker_client.py` — SageMaker API client
- `src/trustforge/sagemaker_submit.py` — Training job submitter
- `src/trustforge/training_backend.py` — Unified training backend interface
- **Status**: ✅ Implemented (merged to develop)

### AgentCoreRuntime
- `src/trustforge/agent/agentcore_adapter.py` — AgentCore adapter
- `src/trustforge/backend_registry.py` — Provider registry (builtin/agentcore)
- **Status**: ✅ Adapter ready; routing through registry

### Composition Root
- `src/trustforge/composition_root.py` — AppContext (offline/live/staging)
- **Status**: ✅ Foundation established

## Trust Kernel 隔離

### Current State
- Core scoring in `src/trustforge_core/` — `scoring.py`, `aggregation.py`, `contracts.py`
- At risk: 429 lines with provider/LLM references mixed into scoring

### Recommended
- #381: Extract pure scoring kernels with zero external dependency
- Separate I/O-bound provider calls from deterministic scoring functions

## 實作盤點

| Module | Status | Notes |
|--------|--------|-------|
| EcoLink | ✅ ImpactPath evaluator deployed | Multi-hop BFS with evidence binding |
| Peer Metrics | ✅ Scheduler + cache deployed | TPS/Gas/TVL connectors operational |
| Training Pipeline | ✅ SageMaker backend | Semi-automated pipeline |
| Narrative i18n | ✅ 11 locales | `narrative_locale.py` |
| Demo Evidence | ✅ Playwright script available | `scripts/demo_evidence_capture.py` |
