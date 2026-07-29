# 實作任務：Analysis Runtime Integration 與 Execution Lineage

> Issue: #922 | Epic: #914

## Task 1: 建立 agos_runtime.py 模組

- [ ] 建立 `src/trustforge/agos_runtime.py`
- [ ] 實作 `agos_enabled() -> bool`（env var check）
- [ ] 實作 `AgosRuntime` class（lazy init of memory_repo, skill_loader, tool_registry, context_builder）
- [ ] 實作 `AgosLineageQuery` class（run-scoped query interface）

## Task 2: 實作 Context Manifest Hook

- [ ] 實作 `AgosRuntime.build_context(run_id, coin, question, ...) -> ContextManifest | None`
  - Call MemoryRetrievalAdapter
  - Call SkillLoader.freeze_manifest
  - Call ContextBuilder.build
  - Wrapped in try/except for graceful degradation
- [ ] 實作 skill selection event emission

## Task 3: 實作 Tool Invocation Audit Wrapper

- [ ] 實作 `tool_audited_fetch(tool_id, fetch_fn, args, *, run_id, tool_registry) -> result`
  - record_invocation(pending)
  - Execute fetch
  - complete_invocation(success/failed)
- [ ] Identify existing connector calls to wrap（ingestion connectors）
- [ ] Add audit wrappers to key connectors（behind feature flag）

## Task 4: 整合進 analysis_flow / orchestrator

- [ ] 在 analysis run 入口加入 context manifest build hook
- [ ] 在 run 結束加入 lineage finalization hook
- [ ] 確保 feature flag OFF → zero Agent OS overhead
- [ ] 確保 graceful degradation on error

## Task 5: 實作 Lineage Query

- [ ] 實作 `get_run_context(run_id) -> ContextManifest | None`
- [ ] 實作 `get_run_memories(run_id) -> list[MemoryRef]`
- [ ] 實作 `get_run_skills(run_id) -> FrozenSkillManifest | None`
- [ ] 實作 `get_run_invocations(run_id) -> list[ToolInvocation]`

## Task 6: 測試

- [ ] 建立 `tests/test_agos_runtime.py`
- [ ] 測試 full run with AGOS_ENABLED=1 → manifest + lineage produced
- [ ] 測試 AGOS_ENABLED=0 → no Agent OS calls
- [ ] 測試 graceful degradation: component failure → run continues
- [ ] 測試 tool_audited_fetch success path
- [ ] 測試 tool_audited_fetch failure path
- [ ] 測試 lineage query returns correct data
- [ ] 建立 `tests/test_agos_integration.py`
- [ ] Integration test with fixture: run → manifest → lineage queryable

## Task 7: 回歸驗證

- [ ] 確認 Trust Kernel scoring inputs unchanged
- [ ] 確認 existing question_bank tests pass
- [ ] 確認 existing dialogue tests pass
- [ ] 確認 report generation unchanged
- [ ] 確認 lint / type-check 通過
- [ ] 確認 pre-push gate 通過
