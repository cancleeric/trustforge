# Issue #998 Phase 3 — fresh Eye review

- Review route: Eye CLI plus actual-browser visual inspection
- Source commit reviewed: `06a4c3d0840a6ea2739a47253fe8c4f3c7393a9e`
- Date: 2026-07-30
- Scope: tracks E and I frontend surfaces only
- Feature changes: none
- Historical rows rewritten: none

## Disposition

**BLOCKED_BROWSER_UNAVAILABLE / NO VISUAL PASS**

The Eye CLI source and breaking-change checks and the focused frontend tests
completed successfully. They are static and programmatic evidence only. The
browser-control inventory available to the CEO was empty (`list=[]`), so no
actual branch could be inspected at desktop, mobile, 200% zoom, or in either
locale. Playwright was deliberately not used as a substitute for the required
human-visible Eye inspection.

This review therefore does not satisfy I-7 and does not convert the historical
Eye gaps on E or I into PASS.

## Static Eye evidence

All commands ran from the repository root against the source commit above.

```text
eye --human frontend/src/components/AssetIntrinsicShadowPanel.tsx
```

Result: exit 0; 49 direct imports, references, and callers reported. The caller
set includes `AnalysisReportView`, the focused component tests, and
`AssetIntrinsicEyeHarness`.

```text
eye --human frontend/src/components/AnalysisReportView.tsx
```

Result: exit 0; 9 direct imports, references, and callers reported. The caller
set includes the intrinsic integration test, `MultiAngleOverview`,
`AnalyzePage`, and `ComparePage`.

```text
eye breaking-changes \
  --from f92810c30319cc4f89cdeb1834dca71b34ea7511 \
  --to 06a4c3d0840a6ea2739a47253fe8c4f3c7393a9e \
  --human --no-exit-code

eye breaking-changes \
  --from 2ffe69fd239b5fd7dd27a2056f20663842aea3fb \
  --to 06a4c3d0840a6ea2739a47253fe8c4f3c7393a9e \
  --human --no-exit-code
```

Result: both informational scans exited 0. The ranges cover the historical E
and I merge commits through current `develop`. Repository-wide findings were
reported and are not represented here as a visual approval or as proof that
unrelated APIs are safe.

## Focused frontend regression evidence

```text
npm test -- --run \
  src/components/AssetIntrinsicShadowPanel.test.tsx \
  src/components/AnalysisReportView.intrinsic.test.tsx \
  --reporter=verbose
```

Result: exit 0; 2 test files and 17 tests passed.

The package installation preceding this run reported Node `v23.6.1` engine
warnings for dependencies that support Node 20, 22, or 24+, and two high
severity `npm audit` findings. Those pre-existing environment/dependency
signals are not hidden, and this docs-only audit does not modify dependencies.

## Required visual matrix

| Surface/state | zh-TW | en | Result |
|---|---:|---:|---|
| Desktop layout | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |
| Mobile layout | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |
| 200% zoom | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |
| Long provenance and overflow | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |
| Shadow/unknown/stale/conflicted state transitions | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |
| Malformed/error/fail-closed state | not inspected | not inspected | `BLOCKED_BROWSER_UNAVAILABLE` |

## Criterion boundary

- E-1–E-6 retain `HISTORICAL_GAP`; this fresh source review does not backdate
  the missing contemporaneous Eye artifact on PR #903.
- I-1–I-6 retain `HISTORICAL_GAP`; static tests and source blast-radius results
  do not replace visual inspection.
- I-7 is `BLOCKED_BROWSER_UNAVAILABLE` for this Phase 3 attempt and still
  requires a fresh actual-branch Eye run across the complete matrix.
- The `/codex-review` I-3 authenticity finding remains independent and open;
  this review neither fixes nor clears it.
- #748 and #998 remain open and the candidate remains shadow-only.
