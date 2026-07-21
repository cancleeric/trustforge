# Archived: deploy skill family

This directory contains the **archived** `deploy` family skill artifact.
It is preserved for git history reference only.

## Status

- **Not active in runtime** — the `deploy` family is in `FORBIDDEN_FAMILIES`
  and will be rejected by both `validate_artifact()` and the policy guard layer.
- **Cannot be staged/approved** — any attempt to write a `deploy` artifact
  through the skill change system will raise a `ValueError`.
- **Historical reference only** — the original artifact documented EC2
  deployment health-check procedures, which are now handled by infrastructure
  scripts outside the outer-skill mechanism.

## Reason for archival

Issue #383: The `deploy` family was never in `SKILL_FAMILIES` (the allowlist
for runtime policy execution), creating an orphaned artifact that could be
misinterpreted as executable.  Archival makes the boundary explicit.

## Related

- Issue #383: Outer Skill policy executor 與 deploy 邊界
- `src/trustforge/skills.py` — `FORBIDDEN_FAMILIES`
- `src/trustforge/policy/guards.py` — `check_family()`
