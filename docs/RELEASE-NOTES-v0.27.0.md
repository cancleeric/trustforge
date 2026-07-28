# TrustForge v0.27.0

Release date: 2026-07-29

## Highlights

- Official two-asset comparison now has a typed report contract, normalized evidence, deterministic fallback, Bedrock synthesis, public API/CLI/Lambda output, a unified report view, and Markdown/HTML export.
- Multi-angle analysis now integrates five analysis directions across backend orchestration, progress states, frontend overview, drilldown, localization, and narration.
- The Trust Kernel has a release-level A/B router, immutable artifacts, canary controls, signed receipts, immediate rollback to the previous approved release, and one authoritative production judgment boundary.
- Whale activity, asset-intrinsic shadow evidence, competition question selection, and report-download workflows are available in the React interface.
- The local pre-push gate uses deterministic parallel and serial backend lanes while preserving full coverage enforcement and a serial rollback mode.

## Quality evidence

- Verified develop merge: `642b23edd9f9c8dcc2797c4fb75ba4653c9fb792`.
- Backend: 5,378 passed, 12 skipped, 1 expected xfail; serial lane 3 passed and 1 skipped.
- Coverage: 84%.
- Competition QA: 24/24.
- Frontend: 68 files and 527 tests.
- Data contracts, source-stub scan, lint, production build, and diff checks passed.

## Operations and rollback

- Production deployment remains a controlled local operation; GitHub Actions deployment stays disabled.
- Release identity is immutable. Keep the previous approved application release as A while v0.27.0 is evaluated as B.
- Canary regression must stop promotion and return the router to A.
- Do not retire compatibility facades without a verified consumer inventory.

## Known follow-up work

- Open issues that describe later enhancements, external-data dependencies, or additional observation remain tracked independently from this release.
- Research-only reliability and conformal promotion remain fail-closed until heterogeneous source requirements are met.
