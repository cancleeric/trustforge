# SkillHub Note: Pre-Push and Release Gates

Use this note when preparing or reviewing TrustForge PRs.

1. Confirm `.githooks/pre-push` is the mandatory local gate and that GitHub
   Actions workflows remain disabled.
2. Run targeted verification for the change, then run or document the full
   pre-push hook evidence before push.
3. Put commit-bound evidence in the PR body or a PR comment: commit SHA, UTC
   timestamp, command, gate result, targeted checks, and any skipped gate reason.
4. Request a named reviewer and post `/codex-review` on every PR.
5. For UI changes, attach actual-branch Eye scan and desktop/mobile verification.
6. For security-sensitive changes, require harper (CISO) and gray (CPO) review.
   For cost-sensitive changes, require harper review.
7. Keep production deployment in the explicit release workflow outside GitHub
   Actions.

Canonical details live in `docs/governance/PRE_PUSH_RELEASE_GATES.md`.
