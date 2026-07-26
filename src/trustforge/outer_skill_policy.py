"""Outer Skill Policy Executor — restricted runtime guard for external skills."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillPolicy:
    """Policy gate for Outer Skill execution.

    Each external skill must pass this policy check before being invoked.
    """
    name: str
    max_duration_sec: int = 30
    max_output_bytes: int = 64 * 1024
    allowed_modules: tuple[str, ...] = ()

    def check(self, skill_name: str, args: dict[str, Any]) -> bool:
        return skill_name == self.name and len(str(args)) < self.max_output_bytes


@dataclass
class PolicyRegistry:
    """Registry of registered Outer Skill policies."""
    _policies: dict[str, SkillPolicy] = field(default_factory=dict)

    def register(self, policy: SkillPolicy) -> None:
        self._policies[policy.name] = policy

    def is_allowed(self, skill_name: str, args: dict[str, Any]) -> bool:
        policy = self._policies.get(skill_name)
        return policy is not None and policy.check(skill_name, args)

    def count(self) -> int:
        return len(self._policies)


# Default policies
default_registry = PolicyRegistry()
default_registry.register(SkillPolicy(
    name="calibrate",
    max_duration_sec=60,
    allowed_modules=("trustforge_core.scoring",),
))
default_registry.register(SkillPolicy(
    name="backfill",
    max_duration_sec=300,
    allowed_modules=("trustforge.backfill",),
))
