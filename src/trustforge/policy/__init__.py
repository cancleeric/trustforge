"""Outer Skill Policy Executor — typed, fail-closed, read-only.

This package implements the policy layer between approved outer-skill artifacts
and the pipeline runtime.  Policies are frozen dataclasses resolved at run start;
staged (unapproved) revisions are invisible to formal runs.

Public API:
    PolicyExecutor    — resolve effective policies and produce execution log snapshots
    SecurityError     — raised on any fail-closed guard rejection

Design invariants:
    - Executor is read-only: it projects policy values, never mutates state
    - Guards are fail-closed: unknown/forbidden/injection → reject, never partial-apply
    - Core controls (trust weights, PIT, evidence binding) cannot be overridden
    - requires_human_approval = True for all policy changes
"""
from __future__ import annotations

from .executor import PolicyExecutor
from .guards import SecurityError

__all__ = ["PolicyExecutor", "SecurityError"]
