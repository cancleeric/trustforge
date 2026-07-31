// Formal analysis (POST /api/analysis-question → /api/analysis-job poll) is
// WIP: formal_run_coordinator.submit() computes a job_id via
// plan_formal_manual() but never calls enqueue_formal_projection(), so the
// job is never persisted and the poll returns 404 job_not_found — surfacing
// as "服務異常". AnalyzePage gates the submit behind this flag until the
// formal-run submit→enqueue integration is complete. Tests mock this module
// to false to exercise the submit flow. Flip to false once formal-run is
// production-ready.
export const ANALYSIS_FORMAL_WIP = true
