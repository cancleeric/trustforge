# 實作任務：Tool Capability Registry Schema 與 Invocation Audit

> Issue: #918 | Epic: #914

## Task 1: 建立 tool_registry.py 模組骨架

- [x] 建立 `src/trustforge/tool_registry.py`
- [x] 實作 `ToolCapability` dataclass
- [x] 實作 `ToolInvocation` dataclass
- [x] 定義 constants：`VALID_SIDE_EFFECTS`, `VALID_EVIDENCE_CLASSES`, `VALID_APPROVAL_REQS`, `VALID_STATUSES`
- [x] 實作 `default_db_path() -> Path`
- [x] 實作 `invocation_input_hash(tool_id, args) -> str`
- [x] 實作 `invocation_output_hash(output) -> str`

## Task 2: 實作 Migration

- [x] 實作 `upgrade(conn) -> None`
  - `_meta` table
  - `tool_capabilities` table（含 CHECK constraints）
  - `tool_invocations` table（含 FK、CHECK constraints）
  - Indexes on `(run_id)`, `(tool_id, started_at)`
- [x] 實作 `rollback(conn) -> None`

## Task 3: 實作 ToolRegistryRepository

- [x] `__init__`（db_path, lazy connection）
- [x] `ensure_schema()`
- [x] `register_tool(cap: ToolCapability) -> None`
  - Validate approval invariant：external_write/deploy → approval=always
  - Duplicate tool_id → raise error
- [x] `get_tool(tool_id) -> ToolCapability | None`
- [x] `list_tools(*, side_effect_class=None) -> list[ToolCapability]`
- [x] `is_known(tool_id) -> bool`
- [x] `requires_approval(tool_id) -> bool`（unknown → True）
- [x] `can_produce_evidence(tool_id) -> bool`
- [x] `record_invocation(inv: ToolInvocation) -> None`
- [x] `complete_invocation(invocation_id, *, output_hash, status, error=None) -> None`
- [x] `get_invocations_by_run(run_id) -> list[ToolInvocation]`
- [x] `get_invocation(invocation_id) -> ToolInvocation | None`
- [x] `close()`

## Task 4: 單元測試

- [x] 建立 `tests/test_tool_registry.py`
- [x] 測試 migration upgrade/rollback
- [x] 測試 register_tool + get_tool round-trip
- [x] 測試 is_known: registered → True; unknown → False
- [x] 測試 requires_approval: read_only → False
- [x] 測試 requires_approval: external_write → True
- [x] 測試 requires_approval: unknown → True (fail-closed)
- [x] 測試 approval invariant enforcement: external_write + approval≠always → error
- [x] 測試 can_produce_evidence: context_only → False; candidate_evidence → True
- [x] 測試 record_invocation + complete_invocation round-trip
- [x] 測試 get_invocations_by_run
- [x] 測試 duplicate tool_id → error
- [x] 確認不 import trust/ / skills.py / outer_skill_policy.py
- [x] 確認 pre-push 通過

### HEAD evidence

Implemented by `src/trustforge/tool_registry.py`; fail-closed lookup,
approval/evidence invariants, invocation audit, hashes, and persistence are
covered by `tests/test_tool_registry.py`.
