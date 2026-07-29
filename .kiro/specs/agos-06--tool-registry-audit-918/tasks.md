# 實作任務：Tool Capability Registry Schema 與 Invocation Audit

> Issue: #918 | Epic: #914

## Task 1: 建立 tool_registry.py 模組骨架

- [ ] 建立 `src/trustforge/tool_registry.py`
- [ ] 實作 `ToolCapability` dataclass
- [ ] 實作 `ToolInvocation` dataclass
- [ ] 定義 constants：`VALID_SIDE_EFFECTS`, `VALID_EVIDENCE_CLASSES`, `VALID_APPROVAL_REQS`, `VALID_STATUSES`
- [ ] 實作 `default_db_path() -> Path`
- [ ] 實作 `invocation_input_hash(tool_id, args) -> str`
- [ ] 實作 `invocation_output_hash(output) -> str`

## Task 2: 實作 Migration

- [ ] 實作 `upgrade(conn) -> None`
  - `_meta` table
  - `tool_capabilities` table（含 CHECK constraints）
  - `tool_invocations` table（含 FK、CHECK constraints）
  - Indexes on `(run_id)`, `(tool_id, started_at)`
- [ ] 實作 `rollback(conn) -> None`

## Task 3: 實作 ToolRegistryRepository

- [ ] `__init__`（db_path, lazy connection）
- [ ] `ensure_schema()`
- [ ] `register_tool(cap: ToolCapability) -> None`
  - Validate approval invariant：external_write/deploy → approval=always
  - Duplicate tool_id → raise error
- [ ] `get_tool(tool_id) -> ToolCapability | None`
- [ ] `list_tools(*, side_effect_class=None) -> list[ToolCapability]`
- [ ] `is_known(tool_id) -> bool`
- [ ] `requires_approval(tool_id) -> bool`（unknown → True）
- [ ] `can_produce_evidence(tool_id) -> bool`
- [ ] `record_invocation(inv: ToolInvocation) -> None`
- [ ] `complete_invocation(invocation_id, *, output_hash, status, error=None) -> None`
- [ ] `get_invocations_by_run(run_id) -> list[ToolInvocation]`
- [ ] `get_invocation(invocation_id) -> ToolInvocation | None`
- [ ] `close()`

## Task 4: 單元測試

- [ ] 建立 `tests/test_tool_registry.py`
- [ ] 測試 migration upgrade/rollback
- [ ] 測試 register_tool + get_tool round-trip
- [ ] 測試 is_known: registered → True; unknown → False
- [ ] 測試 requires_approval: read_only → False
- [ ] 測試 requires_approval: external_write → True
- [ ] 測試 requires_approval: unknown → True (fail-closed)
- [ ] 測試 approval invariant enforcement: external_write + approval≠always → error
- [ ] 測試 can_produce_evidence: context_only → False; candidate_evidence → True
- [ ] 測試 record_invocation + complete_invocation round-trip
- [ ] 測試 get_invocations_by_run
- [ ] 測試 duplicate tool_id → error
- [ ] 確認不 import trust/ / skills.py / outer_skill_policy.py
- [ ] 確認 pre-push 通過
