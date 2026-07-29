"""Runtime lineage and Admin API must disclose the same frozen records."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_admin_api import dispatch_admin_agos
from trustforge.agos_runtime import AgosRuntime
from trustforge.memory_os import MemoryEntry
from trustforge.memory_retrieval import MemoryRef
from trustforge.skill_registry import SkillRevision, TaskSkill, revision_hash_for
from trustforge.tool_registry import ToolCapability


@pytest.fixture(autouse=True)
def _authorize_test_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trustforge.agos_db_auth.verify_db_authorization", lambda purpose: None
    )


def test_runtime_memory_skill_tool_and_context_equal_admin_api(tmp_path: Path) -> None:
    with patch.dict(
        os.environ,
        {"TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_ADMIN_TOKEN": "lineage-token"},
    ):
        runtime = AgosRuntime(data_dir=tmp_path)
        runtime._ensure_init()
        try:
            memory = MemoryEntry(
                memory_id="lineage-memory",
                kind="episodic",
                provider="coingecko",
                content_hash="b" * 64,
                content_ref="BTC price observation",
                published_at="2026-07-01T00:00:00Z",
                retrieved_at="2026-07-01T00:01:00Z",
                evidence_eligible=True,
                run_id="lineage-run",
            )
            runtime._memory_repo.save(memory)
            skill_content = {"family": "analysis", "rules": ["lineage"]}
            skill_hash = revision_hash_for(skill_content)
            runtime._skill_registry.save_skill(
                TaskSkill(
                    skill_id="lineage-skill",
                    family="analysis",
                    name="Lineage",
                    lifecycle="active",
                )
            )
            runtime._skill_registry.save_revision(
                SkillRevision(
                    revision_hash=skill_hash,
                    skill_id="lineage-skill",
                    content=skill_content,
                    is_active=True,
                )
            )
            manifest = runtime.build_context(
                "lineage-run",
                question="BTC?",
                memory_refs=[
                    MemoryRef(
                        memory_id=memory.memory_id,
                        kind=memory.kind,
                        rank=1,
                        reason="fixture",
                        evidence_eligible=True,
                        content_preview=memory.content_ref,
                        run_id="lineage-run",
                    )
                ],
                skill_ids=["lineage-skill"],
            )
            runtime._tool_registry.register_tool(
                ToolCapability(tool_id="lineage-tool", name="Lineage tool")
            )
            invocation_id = runtime.record_tool_invocation(
                "lineage-run", "lineage-tool", {"q": "BTC"}
            )
            runtime.complete_tool_invocation(
                invocation_id, output={"ok": True}, status="success"
            )

            headers = {"Authorization": "Bearer lineage-token"}
            responses = {}
            for name in ("context", "memories", "skills", "tools"):
                status, body = dispatch_admin_agos(
                    f"/api/admin/agos/{name}",
                    "run_id=lineage-run&show_content=true",
                    headers,
                    runtime,
                )
                assert status == 200
                responses[name] = body["data"]

            direct_context = runtime.lineage.get_run_context("lineage-run")
            assert manifest == direct_context
            assert responses["context"]["content_hash"] == direct_context.content_hash
            assert responses["context"]["included_refs"] == direct_context.included_refs.to_dict()

            direct_memories = runtime.lineage.get_run_memories("lineage-run")
            api_memories = responses["memories"]["items"]
            assert [item["memory_id"] for item in api_memories] == [
                item["memory_id"] for item in direct_memories
            ]
            assert api_memories[0]["content_ref"] == direct_memories[0]["content_ref"]

            direct_skills = runtime.lineage.get_run_skills("lineage-run")
            assert [(item["skill_id"], item["revision_hash"]) for item in responses["skills"]["items"]] == [
                (entry.skill_id, entry.revision_hash) for entry in direct_skills.entries
            ]

            direct_tools = runtime.lineage.get_run_invocations("lineage-run")
            api_tools = responses["tools"]["items"]
            lineage_fields = (
                "invocation_id",
                "tool_id",
                "input_hash",
                "output_hash",
                "status",
                "error",
            )
            assert [
                {key: item[key] for key in lineage_fields} for item in api_tools
            ] == [
                {key: item[key] for key in lineage_fields} for item in direct_tools
            ]
        finally:
            runtime.close()
