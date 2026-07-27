from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Sequence


OBSERVATION_WINDOW_HOURS = 24
CANARY_WINDOW_MINUTES = 10

PROTECTED_POINTERS = frozenset({"pointers/active.json", "pointers/candidate.json", "pointers/previous.json"})


@dataclass
class RetentionPolicy:
    observation_window_hours: int = OBSERVATION_WINDOW_HOURS
    canary_window_minutes: int = CANARY_WINDOW_MINUTES

    def protected_set(
        self,
        index_entries: Sequence[dict],
        *,
        now: datetime | None = None,
    ) -> set[str]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.observation_window_hours)
        canary_cutoff = now - timedelta(minutes=self.canary_window_minutes)

        protected: set[str] = set()

        [e for e in index_entries if e.get("digit") and e["digit"] in index_entries]

        for entry in index_entries:
            digest = entry.get("digest", "")
            ts_str = entry.get("timestamp", "")

            if self._is_pointer_referenced(entry):
                protected.add(digest)
                continue

            if not ts_str:
                protected.add(digest)
                continue

            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                protected.add(digest)
                continue

            if ts >= cutoff:
                protected.add(digest)
                continue

            if ts >= canary_cutoff:
                protected.add(digest)
                continue

        return {d for d in protected if d}

    @staticmethod
    def _is_pointer_referenced(entry: dict) -> bool:
        refs = entry.get("pointers_referenced", [])
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str) and ref in PROTECTED_POINTERS:
                    return True
        return False


def apply_retention_policy(
    index_entries: Sequence[dict],
    all_artifact_digests: Sequence[str],
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
) -> tuple[set[str], set[str]]:
    if policy is None:
        policy = RetentionPolicy()
    now = now or datetime.now(timezone.utc)

    protected = policy.protected_set(index_entries, now=now)
    for d in all_artifact_digests:
        if _matches_protected_pointers(d, index_entries):
            protected.add(d)

    all_set = set(all_artifact_digests)
    protected_in_all = protected & all_set
    eligible_for_deletion = all_set - protected

    return protected_in_all, eligible_for_deletion


def _matches_protected_pointers(digest: str, index_entries: Sequence[dict]) -> bool:
    for entry in index_entries:
        if entry.get("digest") == digest:
            if RetentionPolicy._is_pointer_referenced(entry):
                return True
    return False


def render_retention_report(
    protected: set[str],
    eligible: set[str],
    index_entries: Sequence[dict],
) -> str:
    lines = [
        "=== Retention Report ===",
        f"Protected artifacts: {len(protected)}",
        f"Eligible for deletion: {len(eligible)}",
    ]
    if eligible:
        lines.append("")
        lines.append("Eligible artifacts:")
        for d in sorted(eligible):
            entry_info = ""
            for e in index_entries:
                if e.get("digest") == d:
                    entry_info = f" (built: {e.get('timestamp', '?')})"
                    break
            lines.append(f"  {d}{entry_info}")
    return "\n".join(lines)
