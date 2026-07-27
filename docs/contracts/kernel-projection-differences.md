# Kernel Projection vs build_report Semantic Differences

Issue #731.  The `KernelOutput → KernelJudgment` projection contract strips the
LLM narrative layer and produces a deterministic, provider-free map.  The
following differences from `agent.orchestrator.build_report` are intentional.

## No narrative (LLM text) — intentional

`build_report` calls `BedrockClient` to generate `market_judgment`, `facts`,
and `inferences` text via LLM.  `KernelJudgment` has no such fields.  The
projection is a pure structural map that downstream callers compose with their
own narrative if needed.

## No facts / market_judgment — intentional

`Report.facts` and `Report.market_judgment` are LLM-generated sections.
`KernelJudgment` omits them entirely.  `key_basis` carries a deterministic
explanation string (`{source} ({kind}) trust={value}`) without LLM.

## No limits/could_flip — intentional

`Report.limits` and `Report.could_flip` are derived in `build_report` from the
evidence pool and LLM reasoning.  The projection does not compute them.

## No insight/hypothesis_ledger — intentional

`Report.insights` (Phase 1 detect_insights) and `Report.hypothesis_ledger`
(D1.5 pro/con evidence ledger) require additional analysis beyond the kernel
output.  The projection does not compute them.

## Evidence 不經去重 — semantic difference

`build_report` deduplicates Evidence by `(source, content_reference, related)`
and keeps the highest-trust entry for each key.  `KernelJudgment` preserves
every `KernelScoredClaim` as a distinct `Evidence` object, matching the
`scored_claims` tuple 1:1.  This is intentional — the projection is a faithful,
lossless representation of the kernel output graph.

## Contrarian as text only — semantic difference

`build_report` maps contrarian claims into full `Evidence` objects (with
`related_claim = "反方／低信任訊號"`).  `KernelJudgment` stores contrarian
claims as `contrarian_texts` (plain `tuple[str, ...]`), reducing payload size
for downstream consumers that only need the text.
