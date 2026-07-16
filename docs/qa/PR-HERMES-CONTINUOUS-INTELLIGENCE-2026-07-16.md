# PR: Hermes Continuous Intelligence

## Outcome

- Replace local JSON cache/cost paths with SQLite-backed local services and
  repeatable migration tools.
- Run a durable snapshot-isolated, five-stage continuous analysis matrix for all
  coins, modes, and active questions.
- Keep UI navigation read-only; preserve last-complete snapshots during swaps.
- Unify coin selection and expose real pipeline telemetry/drilldown.
- Add SQLite question RAG, user/Hermes dialogue, and run/snapshot lineage.
- Use historical pipeline measurements for approval-gated outer-framework
  improvement proposals.
- Clarify left/center/right/bottom interface ownership and improve available
  right-rail space utilization.

## Safety

- Retrieved historical conclusions are non-evidentiary and never enter current
  Trust Evidence.
- Running analysis stays locked to its immutable snapshot.
- Retry state and DLQ survive daemon restarts.
- Improvement remains proposal-only: sandbox/replay plus human approval is
  required; automatic production mutation is forbidden.
- Write endpoints are rate-limited and pending work is capacity-bounded.

## Verification

- Python: `2099 passed, 6 skipped`; total coverage `90.88%`.
- Frontend: `252 passed`.
- TypeScript and Vite production build: passed.
- Live `/api/analysis-question-context`: returned SQLite retrieval, five matches,
  conversation history, `job_id`, and `snapshot_id`.
- Live analysis-flow launch agent: running with last exit code 0.
- Wiki page 3145 updated; HurricaneSoft memory indexed; SkillHub package validated.

## Known external gates

- Browser-control initialization currently fails before page inspection, so no
  new desktop/mobile screenshot is claimed.
- Provider reliability, licensed historical raw-source data, and model/calibrator
  gates remain as documented in the authoritative Hermes backlog.
