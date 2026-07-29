# 設計：Analysis Runtime Integration 與 Execution Lineage

> Issue: #922 | Epic: #914

## 架構決策

### AD-1: Hook Points 而非重構

在 existing analysis flow 中插入 hook points（minimal changes）：

```python
# analysis_flow.py or orchestrator.py (pseudo-code)

def run_analysis(coin, ...):
    run_id = str(uuid4())

    # ── Agent OS hook: context manifest ──
    if agos_enabled():
        manifest = _build_agos_context(run_id, coin, ...)
        _emit_skill_selection_event(run_id, manifest)

    # ... existing pipeline (unchanged) ...
    evidence = score_claims(...)  # Trust Kernel — NOT TOUCHED
    report = build_report(...)

    # ── Agent OS hook: finalize lineage ──
    if agos_enabled():
        _finalize_agos_lineage(run_id)

    return report
```

### AD-2: Feature Flag

```python
def agos_enabled() -> bool:
    return os.getenv("TRUSTFORGE_AGOS_ENABLED", "0") == "1"
```

Default OFF — 不影響 existing deployments。

### AD-3: Graceful Degradation

所有 Agent OS hooks wrapped in try/except:

```python
def _build_agos_context(run_id, coin, ...):
    try:
        # ... build context manifest ...
        return manifest
    except Exception as e:
        logger.warning(f"Agent OS context build failed: {e}")
        return None
```

If Agent OS fails → run continues as before (no manifest, no lineage, but no crash).

### AD-4: Tool Invocation Integration

Wrap existing connector calls in tool audit:

```python
def _tool_audited_fetch(tool_id, fetch_fn, args, *, run_id, tool_registry):
    """Wrap a fetch call with tool invocation audit."""
    if not agos_enabled() or tool_registry is None:
        return fetch_fn(**args)

    inv_id = str(uuid4())
    input_hash = invocation_input_hash(tool_id, args)
    tool_registry.record_invocation(ToolInvocation(
        invocation_id=inv_id, run_id=run_id, tool_id=tool_id,
        input_hash=input_hash, output_hash=None,
        status="pending", started_at=now_iso(), ...
    ))
    try:
        result = fetch_fn(**args)
        output_hash = invocation_output_hash(result) if result else None
        tool_registry.complete_invocation(inv_id, output_hash=output_hash, status="success")
        return result
    except Exception as e:
        tool_registry.complete_invocation(inv_id, output_hash=None, status="failed", error=str(e))
        raise
```

### AD-5: Lineage Query API（internal）

```python
class AgosLineageQuery:
    """Internal query interface for lineage data. Used by Admin API (#923)."""

    def get_run_context(self, run_id: str) -> ContextManifest | None: ...
    def get_run_memories(self, run_id: str) -> list[MemoryRef]: ...
    def get_run_skills(self, run_id: str) -> FrozenSkillManifest | None: ...
    def get_run_invocations(self, run_id: str) -> list[ToolInvocation]: ...
```

### AD-6: Integration Module

新增 `src/trustforge/agos_runtime.py` 作為 integration glue：
- Initializes all Agent OS components (lazy)
- Provides hook functions for analysis flow
- Provides lineage query interface

## 資料流

```
analysis_flow.py
    │
    ├─ agos_enabled()? ─── No → existing flow (unchanged)
    │         │
    │         Yes
    │         ↓
    ├─ _build_agos_context()
    │    ├─ MemoryRetrievalAdapter.retrieve_question_memory()
    │    ├─ SkillLoader.freeze_manifest()
    │    └─ ContextBuilder.build()
    │         ↓
    │    ContextManifest persisted
    │
    ├─ ... existing scoring pipeline (UNCHANGED) ...
    │
    ├─ tool calls audited via _tool_audited_fetch()
    │    └─ ToolRegistry.record/complete_invocation()
    │
    └─ _finalize_agos_lineage()
         └─ execution_log event
```

## 測試策略

`tests/test_agos_runtime.py`：
- Integration test: full run with AGOS_ENABLED=1 produces manifest + lineage
- Feature flag OFF → no Agent OS calls
- Graceful degradation: Agent OS failure → run continues
- Tool audited fetch: success/failure paths
- Lineage query returns correct data for run_id
- Trust scoring inputs unchanged (compare with/without AGOS)
- Existing question_bank/dialogue tests pass

`tests/test_agos_integration.py`：
- End-to-end with fixture data: run → manifest → lineage → queryable
