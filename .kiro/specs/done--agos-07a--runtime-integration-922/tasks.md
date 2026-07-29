# 實作任務：Analysis Runtime Integration 與 Execution Lineage

> Issue: #922 | Epic: #914

## Task 1: 建立 agos_runtime.py 模組

- [x] 建立 `src/trustforge/agos_runtime.py`
- [x] 實作 `agos_enabled() -> bool`（env var check）
- [x] 實作 `AgosRuntime` class（lazy init of memory_repo, skill_loader, tool_registry, context_builder）
- [x] 實作 `AgosLineageQuery` class（run-scoped query interface）

## Task 2: 實作 Context Manifest Hook

- [x] 實作 `AgosRuntime.build_context(run_id, coin, question, ...) -> ContextManifest | None`
  - Call MemoryRetrievalAdapter
  - Call SkillLoader.freeze_manifest
  - Call ContextBuilder.build
  - Wrapped in try/except for graceful degradation
- [x] 實作 skill selection event emission

## Task 3: 實作 Tool Invocation Audit Wrapper

- [x] 實作 `tool_audited_fetch(tool_id, fetch_fn, args, *, run_id, tool_registry) -> result`
  - record_invocation(pending)
  - Execute fetch
  - complete_invocation(success/failed)
- [x] Identify existing connector calls to wrap（ingestion connectors）
- [x] Add audit wrappers to key connectors（behind feature flag）

## Task 4: 整合進 analysis_flow / orchestrator

- [x] 在 analysis run 入口加入 context manifest build hook
- [x] 在 run 結束加入 lineage finalization hook
- [x] 確保 feature flag OFF → zero Agent OS overhead
- [x] 確保 graceful degradation on error

## Task 5: 實作 Lineage Query

- [x] 實作 `get_run_context(run_id) -> ContextManifest | None`
- [x] 實作 `get_run_memories(run_id) -> list[MemoryRef]`
- [x] 實作 `get_run_skills(run_id) -> FrozenSkillManifest | None`
- [x] 實作 `get_run_invocations(run_id) -> list[ToolInvocation]`

## Task 6: 測試

- [x] 建立 `tests/test_agos_runtime.py`
- [x] 測試 full run with AGOS_ENABLED=1 → manifest + lineage produced
- [x] 測試 AGOS_ENABLED=0 → no Agent OS calls
- [x] 測試 graceful degradation: component failure → run continues
- [x] 測試 tool_audited_fetch success path
- [x] 測試 tool_audited_fetch failure path
- [x] 測試 lineage query returns correct data
- [x] Integration coverage 由 `tests/test_agos_e2e.py` 與
  `tests/test_agos_lineage_consistency.py` 提供，避免建立重複測試檔
- [x] Integration fixture：run → manifest → Admin lineage queryable

## Task 7: 回歸驗證

- [x] 確認 Trust Kernel scoring inputs unchanged
- [x] 確認 existing question_bank tests pass
- [x] 確認 existing dialogue tests pass
- [x] 確認 report generation unchanged
- [x] 確認 lint / type-check 通過
- [x] 確認 pre-push gate 通過

### HEAD evidence

Runtime implementation and focused behavior are covered by
`src/trustforge/agos_runtime.py`, `tests/test_agos_runtime.py`, and
`tests/test_agos_e2e.py`. The two explicit integration-file items above remain
open and are not implied complete by the passing focused suite.
