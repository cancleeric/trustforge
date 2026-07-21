# RAG Model Gate Decision

Date: 2026-07-20

## Decision

TrustForge keeps embedding index and reranker work behind a model gate. The active retrieval path remains deterministic question/context retrieval until a future candidate proves measurable value on a time-separated evaluation set.

## Rationale

- TrustForge's moat is the auditable evidence chain, source independence, contradiction handling, and abstain behavior.
- The current retrieval use is non-evidentiary context support; it must not become an untraceable scoring input.
- Embedding/reranker routes add Bedrock/vector-store cost and operational surface area without a current measured gap.
- Any future RAG route must pass the same rule as other model-gated intelligence modules: offline evaluation first, dry-run only, then human approval.

## Gate Contract

`src/trustforge/module_status.py` exposes:

- `embedding_index_model_gate_status`
- `rag_memory_status`
- `reranker_facet_model_gate_status`

`src/trustforge/modelhub_training.py` exposes `model_route_gate_status`, which keeps `bedrock-direct` as the active route and only permits `agentcore-gateway` dry-run when dependency gates pass.

## Non-Goals

- No production vector database is introduced by this decision.
- No automatic route switch is allowed.
- Retrieved context does not become a substitute for source-backed evidence.
