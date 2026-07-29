from __future__ import annotations

from dataclasses import replace

from trustforge.agos_replay import verify_replay
from trustforge.context_builder import (
    ContextManifest,
    IncludedRefs,
    compute_manifest_hash,
)
from trustforge.memory_os import MemoryEntry, memory_content_hash
from trustforge.skill_registry import SkillRevision, revision_hash_for


class _Repository:
    def __init__(self, entries):
        self.entries = entries

    def get(self, key):
        return self.entries.get(key)


class _SkillRegistry(_Repository):
    def get_revision(self, revision_hash):
        return self.entries.get(revision_hash)


def _fixture():
    skill_content = {"family": "analysis", "rules": ["cite primary sources"]}
    skill_hash = revision_hash_for(skill_content)
    revision = SkillRevision(
        revision_hash=skill_hash,
        skill_id="source-policy",
        content=skill_content,
    )

    memory_content = "primary source content"
    memory = MemoryEntry(
        memory_id="memory-1",
        kind="episodic",
        provider="regulator",
        content_hash=memory_content_hash(memory_content),
        content_ref=memory_content,
        published_at="2026-07-01T00:00:00Z",
        retrieved_at="2026-07-02T00:00:00Z",
    )

    included = IncludedRefs(
        question_ref="question:1",
        memory_refs=[{"memory_id": memory.memory_id, "evidence_eligible": True}],
        skill_refs=[
            {
                "skill_id": revision.skill_id,
                "revision_hash": revision.revision_hash,
                "reason": "requested",
            }
        ],
    )
    manifest_hash = compute_manifest_hash("run-1", included, [], 4096, 12)
    manifest = ContextManifest(
        manifest_id="manifest-1",
        run_id="run-1",
        created_at="2026-07-29T00:00:00Z",
        content_hash=manifest_hash,
        token_budget=4096,
        token_used=12,
        included_refs=included,
    )
    return (
        manifest,
        _Repository({memory.memory_id: memory}),
        _SkillRegistry({revision.revision_hash: revision}),
    )


def test_normal_manifest_replay_hashes_match():
    manifest, memories, skills = _fixture()

    result = verify_replay(manifest, memories, skills)

    assert result.passed is True
    assert result.manifest_hash_match is True
    assert result.skill_hash_matches == [("source-policy", True)]
    assert result.memory_hash_matches == [("memory-1", True)]
    assert result.mismatches == []


def test_tampered_manifest_content_is_detected():
    manifest, memories, skills = _fixture()
    manifest.included_refs.question_ref = "question:tampered"

    result = verify_replay(manifest, memories, skills)

    assert result.passed is False
    assert result.manifest_hash_match is False
    assert any(message.startswith("manifest hash:") for message in result.mismatches)


def test_skill_revision_content_hash_is_reproducible_and_tamper_is_detected():
    manifest, memories, skills = _fixture()
    clean = verify_replay(manifest, memories, skills)
    revision = next(iter(skills.entries.values()))
    skills.entries[revision.revision_hash] = replace(
        revision, content={"family": "analysis", "rules": ["tampered"]}
    )

    tampered = verify_replay(manifest, memories, skills)

    assert clean.skill_hash_matches == [("source-policy", True)]
    assert tampered.passed is False
    assert tampered.skill_hash_matches == [("source-policy", False)]
    assert any(message.startswith("skill source-policy:") for message in tampered.mismatches)


def test_memory_content_hash_is_reproducible_and_tamper_is_detected():
    manifest, memories, skills = _fixture()
    clean = verify_replay(manifest, memories, skills)
    memory = memories.entries["memory-1"]
    memories.entries["memory-1"] = replace(memory, content_ref="tampered content")

    tampered = verify_replay(manifest, memories, skills)

    assert clean.memory_hash_matches == [("memory-1", True)]
    assert tampered.passed is False
    assert tampered.memory_hash_matches == [("memory-1", False)]
    assert any(message.startswith("memory memory-1:") for message in tampered.mismatches)


def test_missing_references_fail_closed():
    manifest, _, _ = _fixture()

    result = verify_replay(manifest, _Repository({}), _SkillRegistry({}))

    assert result.passed is False
    assert result.skill_hash_matches == [("source-policy", False)]
    assert result.memory_hash_matches == [("memory-1", False)]
