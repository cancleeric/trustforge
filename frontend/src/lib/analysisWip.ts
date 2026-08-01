// Formal analysis (POST /api/analysis-question → /api/analysis-job poll) is
// Production readiness switch. Keep this explicit so an emergency frontend
// rollback can re-enable the fail-closed preview state without changing the
// submit/poll implementation.
export const ANALYSIS_FORMAL_WIP = false
