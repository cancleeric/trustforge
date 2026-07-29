# 設計：Tool Capability Registry Schema 與 Invocation Audit

> Issue: #918 | Epic: #914

## 架構決策

### AD-1: 獨立模組 `tool_registry.py`

新增 `src/trustforge/tool_registry.py`。不修改既有 `outer_skill_policy.py`。

兩者的關係：
- `outer_skill_policy.py` — runtime policy guard（max_duration, allowed_modules）
- `tool_registry.py` — tool metadata persistence + invocation audit trail

### AD-2: 獨立 SQLite DB

```python
def default_db_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_TOOL_REGISTRY_DB", "data/tool_registry.db"))
```

### AD-3: Invocation Hash 計算

```python
def invocation_input_hash(tool_id: str, args: dict) -> str:
    payload = json.dumps({"tool_id": tool_id, "args": args},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def invocation_output_hash(output: dict | str) -> str:
    if isinstance(output, dict):
        payload = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        payload = output
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

### AD-4: Approval Enforcement 設計

`ToolRegistryRepository` 只負責**查詢**是否需要 approval；
實際的 approval gate（人工確認）由 runtime layer (#922) 負責。

```python
def requires_approval(self, tool_id: str) -> bool:
    tool = self.get_tool(tool_id)
    if tool is None:
        return True  # unknown → treat as requires approval (fail-closed)
    return tool.approval_requirement in ("always",) or \
           tool.side_effect_class in ("external_write", "deploy_or_release")
```

### AD-5: Evidence Class Guard

```python
def can_produce_evidence(self, tool_id: str) -> bool:
    tool = self.get_tool(tool_id)
    if tool is None:
        return False
    return tool.evidence_class in ("candidate_evidence", "trusted_evidence")
```

`context_only` 和 `none` 的 output 不可作為 Evidence 進入 scoring pipeline。
由 Context Builder (#921) 和 Runtime (#922) 在組裝時遵守。

### AD-6: Append-Only Invocation

`tool_invocations` 只允許：
- INSERT（record_invocation）
- UPDATE status/output_hash/completed_at（complete_invocation）

不允許 DELETE。Application-level enforced（no DELETE method exposed）。

## 資料流

```
ToolCapability registration (startup / config)
    │
    ↓
tool_capabilities table
    │
Runtime: is_known(tool_id)? ────── No → REJECT
    │ Yes
    ↓
requires_approval(tool_id)? ────── Yes → await human approval
    │ No (or approved)
    ↓
record_invocation(pending) → tool_invocations table
    │
    ↓ execute tool
    │
complete_invocation(success/failed/timeout) → UPDATE status + output_hash
```

## 測試策略

`tests/test_tool_registry.py`：
- Migration upgrade/rollback
- register_tool + get_tool round-trip
- is_known: registered → True; unknown → False
- requires_approval: read_only → False; external_write → True; unknown → True
- record_invocation + complete_invocation
- get_invocations_by_run
- Approval invariant: external_write must have approval=always
- Evidence class: context_only → can_produce_evidence = False
- Duplicate tool_id → update or error (design: error, re-register requires version bump)
